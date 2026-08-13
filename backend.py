#!/usr/bin/env python3
import argparse
import fcntl
import hmac
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import store, presets
from pipeline.runner import Runner

NAME_RE = re.compile(r'^[A-Za-z0-9_.-]+$')
ACTIONS = {'start', 'stop', 'restart'}

# Utility scripts that live in their own directory next to monitor.sh,
# outside the docker-compose service discovery monitor.sh does.
UTIL_SCRIPTS = {
    'farm': {
        'dir': 'Farm',
        'cmd': lambda action: ['./farm', action],
        # 'init' is registered so the endpoint mirrors farm's real subcommands, but it's a
        # TUI wizard (scripts/configure.py hard-requires a tty) — the web UI never calls it
        # over the API, it just tells the operator to run it manually.
        'actions': {'init', 'down', 'restart', 'status'},
    },
    'firegex': {
        'dir': 'firegex',
        'cmd': lambda action: ['bash', 'firegex-setup.sh', action],
        'actions': {'start', 'stop', 'restart', 'status'},
    },
    'packmate': {
        'dir': 'packmate',
        'cmd': lambda action: ['bash', 'packmate-setup.sh', action],
        # 'configure' is registered so the endpoint mirrors packmate-setup.sh's real
        # subcommands, but it's a TUI wizard (packmate-configure.py hard-requires a tty) —
        # the web UI never calls it over the API, it just tells the operator to run it
        # manually, same as farm's 'init'.
        'actions': {'start', 'stop', 'restart', 'status', 'configure'},
    },
}


class Config:
    monitor_sh = None
    monitor_dir = None
    state_file = None
    log_file = None
    log_lock = None
    token_file = None
    web_dir = None
    token = None
    runner = None


def run_monitor(args, timeout=30):
    return subprocess.run(
        ['bash', Config.monitor_sh] + args,
        capture_output=True, text=True, timeout=timeout,
    )


def spawn_monitor(args):
    subprocess.Popen(
        ['bash', Config.monitor_sh] + args,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def log_event(level, service, msg):
    msg = msg.replace('\n', ' ').replace('\t', ' ')
    ts = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    ts = ts[:-2] + ':' + ts[-2:] if ts[-5] in '+-' else ts
    line = '%s\t%s\t%s\t%s\n' % (ts, level, service, msg)
    with open(Config.log_lock, 'a') as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            with open(Config.log_file, 'a', encoding='utf-8') as f:
                f.write(line)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def spawn_util(name, action):
    info = UTIL_SCRIPTS[name]
    cwd = os.path.join(Config.monitor_dir, info['dir'])
    cmd = info['cmd'](action)
    log_event('info', name, '%s requested via web UI' % action)

    def runner():
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1800)
            output = (r.stdout + r.stderr).strip().splitlines()
            summary = output[-1] if output else ''
            level = 'success' if r.returncode == 0 else 'error'
            log_event(level, name, '%s finished (exit %d): %s' % (action, r.returncode, summary))
        except subprocess.TimeoutExpired:
            log_event('error', name, '%s timed out' % action)
        except OSError as e:
            log_event('error', name, '%s failed to start: %s' % (action, e))

    threading.Thread(target=runner, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = 'MonitorHTTP/1.0'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype):
        with open(path, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return False
        return hmac.compare_digest(auth[len('Bearer '):].strip(), Config.token)

    def _read_json_body(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            return None
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _service_exists(self, name):
        try:
            with open(Config.state_file) as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False
        return name in state.get('services', {})

    def _read_logs(self, tail):
        try:
            with open(Config.log_file) as f:
                lines = f.readlines()[-tail:]
        except OSError:
            lines = []
        entries = []
        for line in lines:
            parts = line.rstrip('\n').split('\t', 3)
            if len(parts) == 4:
                entries.append({'time': parts[0], 'level': parts[1], 'service': parts[2], 'msg': parts[3]})
        return entries

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith('/api/'):
            if not self._authed():
                return self._send_json(401, {'error': 'unauthorized'})
            return self._api_get(path, parse_qs(parsed.query))
        return self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith('/api/'):
            return self._send_json(404, {'error': 'not found'})
        if not self._authed():
            return self._send_json(401, {'error': 'unauthorized'})
        return self._api_post(path)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith('/api/'):
            return self._send_json(404, {'error': 'not found'})
        if not self._authed():
            return self._send_json(401, {'error': 'unauthorized'})
        if path == '/api/boards':
            body = self._read_json_body()
            if body is None:
                return self._send_json(400, {'error': 'bad json'})
            try:
                boards = Config.runner.store.rename_board(
                    (body.get('old') or '').strip(), body.get('new', ''))
            except KeyError:
                return self._send_json(404, {'error': 'unknown board'})
            except store.ValidationError as e:
                return self._send_json(400, {'error': str(e)})
            return self._send_json(200, {'ok': True, 'boards': boards})
        m = re.match(r'^/api/blocks/([^/]+)$', path)
        if not m:
            return self._send_json(404, {'error': 'not found'})
        name = m.group(1)
        if not NAME_RE.match(name):
            return self._send_json(400, {'error': 'bad name'})
        if not Config.runner.store.get_block(name):
            return self._send_json(404, {'error': 'unknown block'})
        body = self._read_json_body()
        if body is None:
            return self._send_json(400, {'error': 'bad json'})
        fields = self._block_fields(body)
        if 'name' in body:
            fields['name'] = body['name']
        try:
            Config.runner.store.update_block(name, fields, script=body.get('script'))
        except (store.ValidationError, store.CycleError) as e:
            return self._send_json(400, {'error': str(e)})
        log_event('info', 'blocks', 'updated block %s' % name)
        return self._send_json(200, {'ok': True})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith('/api/'):
            return self._send_json(404, {'error': 'not found'})
        if not self._authed():
            return self._send_json(401, {'error': 'unauthorized'})
        if path == '/api/boards':
            body = self._read_json_body() or {}
            name = (body.get('name') or '').strip()
            if not Config.runner.store.delete_board(name):
                return self._send_json(400, {'error': 'cannot delete this board'})
            return self._send_json(200, {'ok': True, 'boards': Config.runner.store.list_boards()})
        m = re.match(r'^/api/blocks/([^/]+)$', path)
        if not m:
            return self._send_json(404, {'error': 'not found'})
        name = m.group(1)
        if not NAME_RE.match(name):
            return self._send_json(400, {'error': 'bad name'})
        if Config.runner.is_running(name):
            Config.runner.stop_block(name)
        if not Config.runner.store.delete_block(name):
            return self._send_json(404, {'error': 'unknown block'})
        log_event('info', 'blocks', 'deleted block %s' % name)
        return self._send_json(200, {'ok': True})

    def _api_get(self, path, query):
        if path == '/api/state':
            try:
                with open(Config.state_file, encoding='utf-8') as f:
                    data = f.read()
            except OSError:
                data = json.dumps({'generated_at': None, 'services': {}})
            body = data.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == '/api/logs':
            tail = 200
            if 'tail' in query:
                try:
                    tail = max(1, min(2000, int(query['tail'][0])))
                except ValueError:
                    pass
            return self._send_json(200, {'logs': self._read_logs(tail)})

        m = re.match(r'^/api/compose/([^/]+)$', path)
        if m:
            return self._get_compose(m.group(1))

        if path == '/api/boards':
            return self._send_json(200, {'boards': Config.runner.store.list_boards()})

        if path == '/api/blocks':
            return self._blocks_list()

        m = re.match(r'^/api/blocks/([^/]+)/output$', path)
        if m:
            return self._block_output(m.group(1), query)

        m = re.match(r'^/api/blocks/([^/]+)$', path)
        if m:
            return self._block_get(m.group(1))

        return self._send_json(404, {'error': 'not found'})

    def _get_compose(self, name):
        if not NAME_RE.match(name):
            return self._send_json(400, {'error': 'bad name'})
        try:
            with open(Config.state_file) as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            return self._send_json(500, {'error': 'state unreadable'})
        svc = state.get('services', {}).get(name)
        if not svc or not svc.get('compose_file'):
            return self._send_json(404, {'error': 'unknown service'})
        try:
            with open(svc['compose_file']) as f:
                content = f.read()
        except OSError as e:
            return self._send_json(500, {'error': str(e)})
        return self._send_json(200, {'name': name, 'compose_file': svc['compose_file'], 'content': content})

    # --- script blocks (pipeline) -----------------------------------------

    ALLOWED_BLOCK_FIELDS = (
        'type', 'mode', 'venv', 'requirements', 'args',
        'timeout', 'port', 'start_period', 'depends_on', 'pass_stdout',
        'x', 'y', 'board',
    )

    def _block_fields(self, body):
        return {k: body[k] for k in self.ALLOWED_BLOCK_FIELDS if k in body}

    def _blocks_list(self):
        data = Config.runner.store.load()
        blocks = data['blocks']
        try:
            levels = store.topo_levels(blocks)
        except store.CycleError:
            levels = {bid: 0 for bid in blocks}
        keys = ('id', 'name', 'type', 'mode', 'venv', 'status', 'exit_code',
                'depends_on', 'pass_stdout', 'port', 'timeout', 'started_at',
                'finished_at', 'last_error', 'x', 'y', 'board')
        out = []
        for bid, b in blocks.items():
            item = {k: b.get(k) for k in keys}
            item['level'] = levels.get(bid, 0)
            out.append(item)
        out.sort(key=lambda x: (x['level'], x['id']))
        return self._send_json(200, {'blocks': out, 'boards': data.get('boards', ['default']),
                                     'generated_at': data.get('generated_at')})

    def _block_get(self, name):
        if not NAME_RE.match(name):
            return self._send_json(400, {'error': 'bad name'})
        block = Config.runner.store.get_block(name)
        if not block:
            return self._send_json(404, {'error': 'unknown block'})
        payload = dict(block)
        payload['script'] = Config.runner.store.read_script(block)
        return self._send_json(200, payload)

    def _block_output(self, name, query):
        if not NAME_RE.match(name):
            return self._send_json(400, {'error': 'bad name'})
        if not Config.runner.store.get_block(name):
            return self._send_json(404, {'error': 'unknown block'})
        since = 0
        if 'since' in query:
            try:
                since = max(0, int(query['since'][0]))
            except ValueError:
                pass
        return self._send_json(200, Config.runner.output(name, since))

    def _api_post(self, path):
        m = re.match(r'^/api/services/([^/]+)/(start|stop|restart)$', path)
        if m:
            name, action = m.group(1), m.group(2)
            if not NAME_RE.match(name) or action not in ACTIONS:
                return self._send_json(400, {'error': 'bad request'})
            if not self._service_exists(name):
                return self._send_json(404, {'error': 'unknown service'})
            spawn_monitor([action, name])
            return self._send_json(202, {'ok': True, 'queued': True})

        m = re.match(r'^/api/utils/([^/]+)/([^/]+)$', path)
        if m:
            name, action = m.group(1), m.group(2)
            info = UTIL_SCRIPTS.get(name)
            if not info or action not in info['actions']:
                return self._send_json(400, {'error': 'bad request'})
            spawn_util(name, action)
            return self._send_json(202, {'ok': True, 'queued': True})

        if path == '/api/restart-all':
            spawn_monitor(['restart-all'])
            return self._send_json(202, {'ok': True, 'queued': True})

        if path == '/api/rescan':
            spawn_monitor(['discover'])
            return self._send_json(202, {'ok': True, 'queued': True})

        if path == '/api/full-scan':
            spawn_monitor(['full-scan'])
            return self._send_json(202, {'ok': True, 'queued': True})

        m = re.match(r'^/api/compose/([^/]+)/validate$', path)
        if m:
            name = m.group(1)
            if not NAME_RE.match(name):
                return self._send_json(400, {'error': 'bad name'})
            body = self._read_json_body()
            if body is None:
                return self._send_json(400, {'error': 'bad json'})
            return self._validate_compose(body.get('content', ''))

        m = re.match(r'^/api/compose/([^/]+)$', path)
        if m:
            name = m.group(1)
            if not NAME_RE.match(name):
                return self._send_json(400, {'error': 'bad name'})
            if not self._service_exists(name):
                return self._send_json(404, {'error': 'unknown service'})
            body = self._read_json_body()
            if body is None:
                return self._send_json(400, {'error': 'bad json'})
            content = body.get('content', '')
            apply_ = bool(body.get('apply', False))
            return self._save_compose(name, content, apply_)

        if path == '/api/boards':
            body = self._read_json_body()
            if body is None:
                return self._send_json(400, {'error': 'bad json'})
            name = (body.get('name') or '').strip()
            if not name:
                return self._send_json(400, {'error': 'name required'})
            try:
                boards = Config.runner.store.create_board(name)
            except store.ValidationError as e:
                return self._send_json(400, {'error': str(e)})
            return self._send_json(201, {'ok': True, 'boards': boards})

        if path == '/api/blocks':
            body = self._read_json_body()
            if body is None:
                return self._send_json(400, {'error': 'bad json'})
            name = (body.get('name') or '').strip()
            if not name:
                return self._send_json(400, {'error': 'name required'})
            try:
                block = Config.runner.store.create_block(
                    name, script=body.get('script', ''), **self._block_fields(body))
            except (store.ValidationError, store.CycleError) as e:
                return self._send_json(400, {'error': str(e)})
            log_event('info', 'blocks', 'created block %s' % block['id'])
            return self._send_json(201, {'ok': True, 'id': block['id']})

        m = re.match(r'^/api/blocks/([^/]+)/(run|stop|restart)$', path)
        if m:
            name, action = m.group(1), m.group(2)
            if not NAME_RE.match(name):
                return self._send_json(400, {'error': 'bad name'})
            if not Config.runner.store.get_block(name):
                return self._send_json(404, {'error': 'unknown block'})
            getattr(Config.runner, action + '_block')(name)
            return self._send_json(202, {'ok': True, 'queued': True})

        if path == '/api/pipeline/run':
            try:
                Config.runner.run_pipeline()
            except (store.ValidationError, store.CycleError) as e:
                return self._send_json(400, {'error': str(e)})
            return self._send_json(202, {'ok': True})

        if path == '/api/pipeline/stop':
            Config.runner.stop_pipeline()
            return self._send_json(202, {'ok': True})

        if path == '/api/pipeline/presets':
            body = self._read_json_body() or {}
            which = body.get('set', 'demo')
            if which not in presets.PRESET_SETS:
                return self._send_json(400, {'error': 'unknown preset set'})
            try:
                created = presets.seed(Config.runner.store, which)
            except (store.ValidationError, store.CycleError) as e:
                return self._send_json(400, {'error': str(e)})
            log_event('info', 'blocks', 'loaded %d preset block(s) from %s' % (created, which))
            return self._send_json(201, {'ok': True, 'created': created, 'set': which})

        return self._send_json(404, {'error': 'not found'})

    def _validate_compose(self, content):
        fd, tmp = tempfile.mkstemp(suffix='.yaml', dir=os.path.dirname(Config.state_file))
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(content)
            try:
                r = run_monitor(['validate', tmp], timeout=20)
            except subprocess.TimeoutExpired:
                return self._send_json(504, {'ok': False, 'message': 'validation timed out'})
            ok = r.returncode == 0
            return self._send_json(200, {'ok': ok, 'message': (r.stdout + r.stderr).strip()})
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _save_compose(self, name, content, apply_):
        fd, tmp = tempfile.mkstemp(suffix='.yaml', dir=os.path.dirname(Config.state_file))
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(content)
            args = ['save-compose', name, tmp]
            if apply_:
                args.append('--apply')
            try:
                r = run_monitor(args, timeout=20)
            except subprocess.TimeoutExpired:
                return self._send_json(504, {'ok': False, 'message': 'save timed out'})
            ok = r.returncode == 0
            return self._send_json(200, {'ok': ok, 'message': (r.stdout + r.stderr).strip(), 'applied': apply_ and ok})
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _serve_static(self, path):
        if path == '/':
            path = '/index.html'
        safe_path = os.path.normpath(path).lstrip('/')
        full = os.path.join(Config.web_dir, safe_path)
        web_root = os.path.abspath(Config.web_dir)
        if not os.path.abspath(full).startswith(web_root + os.sep) and os.path.abspath(full) != web_root:
            return self._send_json(403, {'error': 'forbidden'})
        if not os.path.isfile(full):
            return self._send_json(404, {'error': 'not found'})
        ctype, _ = mimetypes.guess_type(full)
        return self._send_file(full, ctype or 'application/octet-stream')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True, help='repo root (unused directly, kept for context)')
    ap.add_argument('--monitor-dir', required=True)
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--bind', default='0.0.0.0')
    args = ap.parse_args()

    Config.monitor_sh = os.path.join(args.monitor_dir, 'monitor.sh')
    Config.monitor_dir = args.monitor_dir
    Config.state_file = os.path.join(args.monitor_dir, 'state', 'state.json')
    Config.log_file = os.path.join(args.monitor_dir, 'state', 'events.log')
    Config.log_lock = os.path.join(args.monitor_dir, 'state', 'events.log.lock')
    Config.token_file = os.path.join(args.monitor_dir, 'state', 'token')
    Config.web_dir = os.path.join(args.monitor_dir, 'web')

    with open(Config.token_file) as f:
        Config.token = f.read().strip()

    fresh_pipeline = not os.path.exists(os.path.join(args.monitor_dir, 'state', 'pipeline.json'))
    Config.runner = Runner(args.monitor_dir, log=log_event)
    if fresh_pipeline:
        try:
            n = presets.seed(Config.runner.store)
            log_event('info', 'blocks', 'seeded %d demo block(s) on first run' % n)
        except Exception as e:
            log_event('error', 'blocks', 'preset seed failed: %s' % e)

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f'monitor backend listening on {args.bind}:{args.port}', file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
