package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"crypto/tls"
	"errors"
	"fmt"
	"html/template"
	"log"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/encoding/gzip"
	"google.golang.org/protobuf/types/known/durationpb"

	"github.com/c4t-but-s4d/neo/v2/internal/client"
	"github.com/c4t-but-s4d/neo/v2/pkg/grpcauth"
	epb "github.com/c4t-but-s4d/neo/v2/proto/go/exploits"
	logspb "github.com/c4t-but-s4d/neo/v2/proto/go/logs"
)

const (
	defaultAddr     = ":8090"
	defaultNeoAddr  = "server:5005"
	defaultClientID = "neo-webui"
	maxUploadBytes  = 32 << 20
)

var validID = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)
var validDownloadExt = regexp.MustCompile(`^\.[A-Za-z0-9]+$`)

type appConfig struct {
	Addr        string
	NeoAddr     string
	GrpcAuthKey string
	WebPassword string
	UseTLS      bool
	ClientID    string
}

type app struct {
	cfg appConfig
	tpl *template.Template
}

type exploitView struct {
	ID         string
	Version    int64
	Entrypoint string
	RunEvery   string
	Timeout    string
	Disabled   bool
	Endless    bool
	IsArchive  bool
}

type bucketView struct {
	Client string
	Teams  []teamView
}

type teamView struct {
	Name string
	IP   string
}

type pageData struct {
	Message    string
	Error      string
	NeoAddr    string
	FarmURL    string
	FlagRegexp string
	Exploits   []exploitView
	Buckets    []bucketView
}

type logsData struct {
	Message string
	Error   string
	ID      string
	Version int64
	Lines   []*logspb.LogLine
}

func main() {
	cfg := appConfig{
		Addr:        env("NEO_WEB_ADDR", defaultAddr),
		NeoAddr:     env("NEO_GRPC_ADDR", defaultNeoAddr),
		GrpcAuthKey: os.Getenv("NEO_GRPC_AUTH_KEY"),
		WebPassword: os.Getenv("NEO_WEB_PASSWORD"),
		ClientID:    env("NEO_WEB_CLIENT_ID", defaultClientID),
		UseTLS:      os.Getenv("NEO_GRPC_TLS") == "1",
	}
	if cfg.WebPassword == "" {
		log.Fatal("NEO_WEB_PASSWORD is required")
	}

	a := &app{
		cfg: cfg,
		tpl: template.Must(template.New("ui").Parse(pageTemplate)),
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/", a.auth(a.index))
	mux.HandleFunc("/upload", a.auth(a.upload))
	mux.HandleFunc("/action", a.auth(a.action))
	mux.HandleFunc("/download", a.auth(a.download))
	mux.HandleFunc("/logs", a.auth(a.logs))

	log.Printf("starting Neo web UI on %s, Neo gRPC %s", cfg.Addr, cfg.NeoAddr)
	if err := http.ListenAndServe(cfg.Addr, mux); err != nil {
		log.Fatal(err)
	}
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func (a *app) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		_, pass, ok := r.BasicAuth()
		if !ok || subtle.ConstantTimeCompare([]byte(pass), []byte(a.cfg.WebPassword)) != 1 {
			w.Header().Set("WWW-Authenticate", `Basic realm="Neo Web UI"`)
			http.Error(w, "authentication required", http.StatusUnauthorized)
			return
		}
		next(w, r)
	}
}

func (a *app) neoClient(ctx context.Context) (*client.Client, *grpc.ClientConn, error) {
	opts := []grpc.DialOption{
		grpc.WithDefaultCallOptions(grpc.UseCompressor(gzip.Name)),
	}
	if a.cfg.GrpcAuthKey != "" {
		interceptor := grpcauth.NewClientInterceptor(a.cfg.GrpcAuthKey)
		opts = append(opts,
			grpc.WithUnaryInterceptor(interceptor.Unary()),
			grpc.WithStreamInterceptor(interceptor.Stream()),
		)
	}
	if a.cfg.UseTLS {
		opts = append(opts, grpc.WithTransportCredentials(credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS12})))
	} else {
		opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))
	}

	conn, err := grpc.DialContext(ctx, a.cfg.NeoAddr, opts...)
	if err != nil {
		return nil, nil, fmt.Errorf("dial Neo gRPC: %w", err)
	}
	return client.New(conn, a.cfg.ClientID), conn, nil
}

func (a *app) index(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	nc, conn, err := a.neoClient(ctx)
	if err != nil {
		a.renderIndex(w, pageData{Error: err.Error(), NeoAddr: a.cfg.NeoAddr})
		return
	}
	defer conn.Close()

	state, err := nc.GetServerState(ctx)
	if err != nil {
		a.renderIndex(w, pageData{Error: err.Error(), NeoAddr: a.cfg.NeoAddr})
		return
	}

	data := pageData{
		Message: r.URL.Query().Get("msg"),
		Error:   r.URL.Query().Get("err"),
		NeoAddr: a.cfg.NeoAddr,
	}
	if state.GetConfig() != nil {
		data.FarmURL = state.GetConfig().GetFarmUrl()
		data.FlagRegexp = state.GetConfig().GetFlagRegexp()
	}
	data.Exploits = exploitViews(state.GetExploits())
	data.Buckets = bucketViews(state.GetClientTeamMap())

	a.renderIndex(w, data)
}

func (a *app) upload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)
	if err := r.ParseMultipartForm(maxUploadBytes); err != nil {
		redirectErr(w, r, fmt.Errorf("parse multipart form: %w", err))
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		redirectErr(w, r, fmt.Errorf("read uploaded file: %w", err))
		return
	}
	defer file.Close()

	exploitID := strings.TrimSpace(r.FormValue("id"))
	if exploitID == "" {
		exploitID = strings.TrimSuffix(filepath.Base(header.Filename), filepath.Ext(header.Filename))
	}
	if err := validateID(exploitID); err != nil {
		redirectErr(w, r, err)
		return
	}

	isArchive := r.FormValue("archive") == "on"
	entrypoint := strings.TrimSpace(r.FormValue("entrypoint"))
	if entrypoint == "" {
		entrypoint = filepath.Base(header.Filename)
	}
	if err := validateEntrypoint(entrypoint, isArchive); err != nil {
		redirectErr(w, r, err)
		return
	}

	runEvery, err := parseDurationField(r.FormValue("interval"), 30*time.Second)
	if err != nil {
		redirectErr(w, r, fmt.Errorf("bad interval: %w", err))
		return
	}
	timeout, err := parseDurationField(r.FormValue("timeout"), 10*time.Second)
	if err != nil {
		redirectErr(w, r, fmt.Errorf("bad timeout: %w", err))
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	nc, conn, err := a.neoClient(ctx)
	if err != nil {
		redirectErr(w, r, err)
		return
	}
	defer conn.Close()

	info, err := nc.UploadFile(ctx, file)
	if err != nil {
		redirectErr(w, r, fmt.Errorf("upload to Neo: %w", err))
		return
	}

	updated, err := nc.UpdateExploit(ctx, &epb.ExploitState{
		ExploitId: exploitID,
		File:      info,
		Config: &epb.ExploitConfiguration{
			Entrypoint: entrypoint,
			IsArchive:  isArchive,
			RunEvery:   durationpb.New(runEvery),
			Timeout:    durationpb.New(timeout),
			Disabled:   r.FormValue("disabled") == "on",
			Endless:    r.FormValue("endless") == "on",
		},
	})
	if err != nil {
		redirectErr(w, r, fmt.Errorf("update exploit: %w", err))
		return
	}

	redirectMsg(w, r, fmt.Sprintf("uploaded %s version %d", updated.GetExploitId(), updated.GetVersion()))
}

func (a *app) action(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	id := strings.TrimSpace(r.FormValue("id"))
	if err := validateID(id); err != nil {
		redirectErr(w, r, err)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()

	nc, conn, err := a.neoClient(ctx)
	if err != nil {
		redirectErr(w, r, err)
		return
	}
	defer conn.Close()

	switch r.FormValue("action") {
	case "enable":
		err = nc.SetExploitDisabled(ctx, id, false)
	case "disable":
		err = nc.SetExploitDisabled(ctx, id, true)
	case "delete":
		err = nc.DeleteExploit(ctx, id)
	case "single":
		err = nc.SingleRun(ctx, id)
	case "update":
		err = updateConfig(ctx, nc, id, r)
	default:
		err = errors.New("unknown action")
	}
	if err != nil {
		redirectErr(w, r, err)
		return
	}
	redirectMsg(w, r, fmt.Sprintf("%s: %s", id, r.FormValue("action")))
}

func (a *app) download(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	id := strings.TrimSpace(r.URL.Query().Get("id"))
	if err := validateID(id); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	nc, conn, err := a.neoClient(ctx)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	defer conn.Close()

	resp, err := nc.Exploit(ctx, id)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	state := resp.GetState()
	if state == nil || state.GetFile() == nil {
		http.Error(w, "exploit has invalid empty state", http.StatusInternalServerError)
		return
	}

	var buf bytes.Buffer
	if err := nc.DownloadFile(ctx, state.GetFile(), &buf); err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{
		"filename": downloadFilename(state),
	}))
	w.Header().Set("Content-Length", strconv.Itoa(buf.Len()))
	if _, err := w.Write(buf.Bytes()); err != nil {
		log.Printf("write download response: %v", err)
	}
}

func updateConfig(ctx context.Context, nc *client.Client, id string, r *http.Request) error {
	resp, err := nc.Exploit(ctx, id)
	if err != nil {
		return fmt.Errorf("fetch exploit: %w", err)
	}
	state := resp.GetState()
	if state == nil || state.GetConfig() == nil {
		return errors.New("exploit has invalid empty state")
	}

	runEvery, err := parseDurationField(r.FormValue("interval"), protoDuration(state.GetConfig().GetRunEvery(), 30*time.Second))
	if err != nil {
		return fmt.Errorf("bad interval: %w", err)
	}
	timeout, err := parseDurationField(r.FormValue("timeout"), protoDuration(state.GetConfig().GetTimeout(), 10*time.Second))
	if err != nil {
		return fmt.Errorf("bad timeout: %w", err)
	}

	state.Config.RunEvery = durationpb.New(runEvery)
	state.Config.Timeout = durationpb.New(timeout)
	state.Config.Endless = r.FormValue("endless") == "on"
	state.Config.Disabled = r.FormValue("disabled") == "on"

	if _, err := nc.UpdateExploit(ctx, state); err != nil {
		return fmt.Errorf("update exploit config: %w", err)
	}
	return nil
}

func downloadFilename(state *epb.ExploitState) string {
	name := fmt.Sprintf("%s_v%d", state.GetExploitId(), state.GetVersion())
	cfg := state.GetConfig()
	if cfg == nil {
		return name
	}
	if cfg.GetIsArchive() {
		return name + ".tar.gz"
	}
	if ext := filepath.Ext(path.Base(cfg.GetEntrypoint())); validDownloadExt.MatchString(ext) {
		return name + ext
	}
	return name
}

func (a *app) logs(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.URL.Query().Get("id"))
	if err := validateID(id); err != nil {
		a.renderLogs(w, logsData{Error: err.Error(), ID: id})
		return
	}
	version, err := strconv.ParseInt(r.URL.Query().Get("version"), 10, 64)
	if err != nil || version <= 0 {
		a.renderLogs(w, logsData{Error: "bad version", ID: id})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	nc, conn, err := a.neoClient(ctx)
	if err != nil {
		a.renderLogs(w, logsData{Error: err.Error(), ID: id, Version: version})
		return
	}
	defer conn.Close()

	ch, err := nc.SearchLogLines(ctx, id, version)
	if err != nil {
		a.renderLogs(w, logsData{Error: err.Error(), ID: id, Version: version})
		return
	}

	var lines []*logspb.LogLine
	for batch := range ch {
		lines = append(lines, batch...)
		if len(lines) >= 300 {
			lines = lines[len(lines)-300:]
		}
	}
	a.renderLogs(w, logsData{ID: id, Version: version, Lines: lines})
}

func validateID(id string) error {
	if id == "" {
		return errors.New("exploit id is required")
	}
	if !validID.MatchString(id) {
		return errors.New("exploit id may contain only letters, digits, dot, underscore, and dash")
	}
	return nil
}

func validateEntrypoint(entrypoint string, isArchive bool) error {
	if entrypoint == "" {
		return errors.New("entrypoint is required")
	}
	clean := path.Clean(entrypoint)
	if strings.HasPrefix(clean, "../") || clean == ".." || path.IsAbs(clean) {
		return errors.New("entrypoint must be a relative path inside the upload")
	}
	if !isArchive && clean != path.Base(clean) {
		return errors.New("single-file upload entrypoint must be a plain filename")
	}
	return nil
}

func parseDurationField(value string, fallback time.Duration) (time.Duration, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return fallback, nil
	}
	if n, err := strconv.Atoi(value); err == nil {
		return time.Duration(n) * time.Second, nil
	}
	return time.ParseDuration(value)
}

func exploitViews(states []*epb.ExploitState) []exploitView {
	out := make([]exploitView, 0, len(states))
	for _, state := range states {
		cfg := state.GetConfig()
		if cfg == nil {
			continue
		}
		out = append(out, exploitView{
			ID:         state.GetExploitId(),
			Version:    state.GetVersion(),
			Entrypoint: cfg.GetEntrypoint(),
			RunEvery:   protoDuration(cfg.GetRunEvery(), 30*time.Second).String(),
			Timeout:    protoDuration(cfg.GetTimeout(), 10*time.Second).String(),
			Disabled:   cfg.GetDisabled(),
			Endless:    cfg.GetEndless(),
			IsArchive:  cfg.GetIsArchive(),
		})
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].ID < out[j].ID
	})
	return out
}

func bucketViews(buckets map[string]*epb.TeamBucket) []bucketView {
	out := make([]bucketView, 0, len(buckets))
	for clientID, bucket := range buckets {
		view := bucketView{Client: clientID}
		for name, ip := range bucket.GetTeams() {
			view.Teams = append(view.Teams, teamView{Name: name, IP: ip})
		}
		sort.Slice(view.Teams, func(i, j int) bool {
			return view.Teams[i].Name < view.Teams[j].Name
		})
		out = append(out, view)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Client < out[j].Client
	})
	return out
}

func protoDuration(value *durationpb.Duration, fallback time.Duration) time.Duration {
	if value == nil {
		return fallback
	}
	return value.AsDuration()
}

func redirectMsg(w http.ResponseWriter, r *http.Request, msg string) {
	http.Redirect(w, r, "/?msg="+urlQueryEscape(msg), http.StatusSeeOther)
}

func redirectErr(w http.ResponseWriter, r *http.Request, err error) {
	http.Redirect(w, r, "/?err="+urlQueryEscape(err.Error()), http.StatusSeeOther)
}

func urlQueryEscape(s string) string {
	return url.QueryEscape(s)
}

func (a *app) renderIndex(w http.ResponseWriter, data pageData) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := a.tpl.ExecuteTemplate(w, "index", data); err != nil {
		log.Printf("render index: %v", err)
	}
}

func (a *app) renderLogs(w http.ResponseWriter, data logsData) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := a.tpl.ExecuteTemplate(w, "logs", data); err != nil {
		log.Printf("render logs: %v", err)
	}
}

const pageTemplate = `
{{define "baseHead"}}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Neo Web UI</title>
  <style>
    :root {
      color-scheme: light;
      --cbs: #333333;
      --text: #202124;
      --muted: #6f6f6f;
      --line: #c7c7c7;
      --field: #9e9e9e;
      --danger: #c10015;
      font-family: "Source Code Pro", "Roboto Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; margin: 0; background: #ffffff; color: var(--text); }
    body { font-family: inherit; font-size: 14px; }
    header { height: 50px; background: var(--cbs); color: #ffffff; box-shadow: 0 2px 5px rgba(0,0,0,.2); }
    main { padding: 16px 15px 32px; }
    h1 { margin: 0; font-size: 22px; line-height: 1; font-weight: 400; letter-spacing: 0; }
    h2 { margin: 0; font-size: 21px; line-height: 1.35; font-weight: 400; letter-spacing: 0; }
    .toolbar { height: 50px; display: flex; align-items: center; padding: 0 16px; gap: 12px; }
    .brandmark { width: 28px; height: 28px; border: 1px solid #ffffff; border-radius: 50%; display: grid; place-items: center; color: #ffffff; font-size: 10px; line-height: 1; }
    .brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
    .tabs { align-self: stretch; display: flex; align-items: stretch; gap: 0; margin-left: auto; margin-right: auto; }
    .tabs a { min-width: 88px; display: grid; place-items: center; padding: 0 16px; color: #ffffff; text-decoration: none; text-transform: uppercase; border-bottom: 2px solid transparent; }
    .tabs a[aria-current="page"], .tabs a:hover { border-bottom-color: #ffffff; background: rgba(255,255,255,.04); }
    .card { background: #ffffff; border-radius: 4px; box-shadow: 0 1px 5px rgba(0,0,0,.2), 0 2px 2px rgba(0,0,0,.14), 0 3px 1px -2px rgba(0,0,0,.12); padding: 32px; margin-bottom: 16px; }
    .card-header { margin-bottom: 32px; }
    .meta-card { padding: 16px 32px; }
    .meta { color: var(--muted); display: flex; gap: 16px 24px; flex-wrap: wrap; }
    .meta span { white-space: nowrap; }
    .form-grid { display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 20px 16px; align-items: start; }
    label { display: block; color: var(--text); }
    .field-label { margin-bottom: 5px; font-size: 14px; }
    input[type="text"], input[type="file"] { width: 100%; min-width: 0; height: 56px; padding: 0 12px; border: 1px solid var(--line); border-radius: 4px; background: #ffffff; color: var(--text); font: inherit; font-size: 16px; }
    input[type="file"] { padding-top: 16px; }
    input[type="text"]:focus, input[type="file"]:focus { outline: 0; border-color: var(--cbs); box-shadow: 0 0 0 1px var(--cbs); }
    input[type="checkbox"] { width: 16px; height: 16px; margin: 0 8px 0 0; accent-color: var(--cbs); }
    .check-label, .table-toggle { min-height: 56px; display: inline-flex; align-items: center; color: var(--text); }
    button, .button { min-height: 36px; display: inline-flex; align-items: center; justify-content: center; padding: 0 16px; border: 0; border-radius: 3px; background: var(--cbs); color: #ffffff; font: inherit; font-weight: 600; text-transform: uppercase; text-decoration: none; cursor: pointer; box-shadow: 0 1px 5px rgba(0,0,0,.2), 0 2px 2px rgba(0,0,0,.14), 0 3px 1px -2px rgba(0,0,0,.12); white-space: nowrap; }
    button:hover, .button:hover { background: #2b2b2b; }
    button.secondary, .button.secondary { background: #ffffff; color: var(--cbs); border: 1px solid #d0d0d0; box-shadow: none; }
    button.secondary:hover, .button.secondary:hover { background: #f5f5f5; }
    button.warn { background: var(--danger); }
    .form-actions { margin: 24px 0 0; }
    .table-card { padding: 0; overflow: hidden; }
    .table-top { min-height: 48px; display: flex; align-items: center; padding: 0 16px; }
    .table-wrap { width: 100%; overflow-x: auto; }
    table { width: 100%; min-width: 980px; border-collapse: collapse; background: #ffffff; }
    th, td { padding: 9px 16px; border-bottom: 1px solid #e0e0e0; text-align: left; vertical-align: middle; }
    th { background: var(--cbs); color: #ffffff; font-weight: 400; text-align: center; }
    tbody tr:last-child td { border-bottom: 0; }
    code, pre { background: #f5f5f5; border-radius: 2px; }
    code { padding: 2px 5px; }
    pre { padding: 12px; overflow: auto; border: 1px solid #eeeeee; }
    .schedule-form { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .small-input { max-width: 84px; height: 34px !important; font-size: 14px !important; }
    .actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .mode, .empty { color: var(--muted); }
    .empty { text-align: center; padding: 16px; }
    .ok, .err { border-radius: 4px; padding: 12px 16px; margin-bottom: 16px; box-shadow: 0 1px 5px rgba(0,0,0,.16); }
    .ok { background: #f1f8e9; color: #33691e; }
    .err { background: #ffebee; color: var(--danger); }
    @media (max-width: 900px) {
      .form-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 600px) {
      main { padding: 16px; }
      header, .toolbar { height: 50px; }
      h1 { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .tabs { margin-left: 8px; margin-right: 0; }
      .tabs a { min-width: 72px; padding: 0 10px; }
      .card { padding: 32px; }
      .meta-card { padding: 16px; }
      .meta span { white-space: normal; }
    }
  </style>
</head>
<body>
<header>
  <div class="toolbar">
    <div class="brand"><span class="brandmark">CBS</span><h1>S4D Neo</h1></div>
    <nav class="tabs" aria-label="Navigation">
      <a href="/" aria-current="page">Exploits</a>
      <a href="#workers">Workers</a>
    </nav>
  </div>
</header>
<main>
{{end}}

{{define "index"}}
{{template "baseHead" .}}
  {{if .Message}}<div class="ok">{{.Message}}</div>{{end}}
  {{if .Error}}<div class="err">{{.Error}}</div>{{end}}

  <section class="card meta-card">
    <div class="meta">
      <span>Neo: <code>{{.NeoAddr}}</code></span>
      <span>Farm: <code>{{.FarmURL}}</code></span>
      <span>Flag regex: <code>{{.FlagRegexp}}</code></span>
    </div>
  </section>

  <section class="card">
    <div class="card-header"><h2>Upload Exploit</h2></div>
    <form method="post" action="/upload" enctype="multipart/form-data">
      <div class="form-grid">
        <label><div class="field-label">File</div><input type="file" name="file" required></label>
        <label><div class="field-label">ID</div><input type="text" name="id" placeholder="my_sploit"></label>
        <label><div class="field-label">Interval</div><input type="text" name="interval" value="30s"></label>
        <label><div class="field-label">Timeout</div><input type="text" name="timeout" value="10s"></label>
        <label><div class="field-label">Entrypoint</div><input type="text" name="entrypoint" placeholder="auto from filename"></label>
        <label class="check-label"><input type="checkbox" name="archive"> Archive upload</label>
        <label class="check-label"><input type="checkbox" name="disabled"> Upload disabled</label>
        <label class="check-label"><input type="checkbox" name="endless"> Endless exploit</label>
      </div>
      <p class="form-actions"><button type="submit">Upload</button></p>
    </form>
  </section>

  <section class="card table-card">
    <div class="table-top"><h2>Exploits</h2></div>
    <div class="table-wrap">
    <table>
      <thead>
        <tr><th>ID</th><th>Version</th><th>Entrypoint</th><th>Schedule</th><th>Mode</th><th>Actions</th></tr>
      </thead>
      <tbody>
      {{range .Exploits}}
        <tr>
          <td><code>{{.ID}}</code></td>
          <td>{{.Version}}</td>
          <td>{{.Entrypoint}}</td>
          <td>
            <form class="schedule-form" method="post" action="/action">
              <input type="hidden" name="id" value="{{.ID}}">
              <input type="hidden" name="action" value="update">
              <input class="small-input" type="text" name="interval" value="{{.RunEvery}}">
              <input class="small-input" type="text" name="timeout" value="{{.Timeout}}">
              <label class="table-toggle"><input type="checkbox" name="endless" {{if .Endless}}checked{{end}}> endless</label>
              <label class="table-toggle"><input type="checkbox" name="disabled" {{if .Disabled}}checked{{end}}> disabled</label>
              <button type="submit" class="secondary">Save</button>
            </form>
          </td>
          <td class="mode">{{if .Disabled}}disabled{{else}}enabled{{end}}{{if .IsArchive}}, archive{{end}}</td>
          <td>
            <div class="actions">
              <form class="inline" method="post" action="/action"><input type="hidden" name="id" value="{{.ID}}"><input type="hidden" name="action" value="single"><button type="submit">Single</button></form>
              {{if .Disabled}}
                <form class="inline" method="post" action="/action"><input type="hidden" name="id" value="{{.ID}}"><input type="hidden" name="action" value="enable"><button type="submit">Enable</button></form>
              {{else}}
                <form class="inline" method="post" action="/action"><input type="hidden" name="id" value="{{.ID}}"><input type="hidden" name="action" value="disable"><button type="submit" class="warn">Disable</button></form>
              {{end}}
              <a class="button secondary" href="/download?id={{.ID}}">Download</a>
              <a class="button secondary" href="/logs?id={{.ID}}&version={{.Version}}">Logs</a>
              <form class="inline" method="post" action="/action" onsubmit="return confirm('Delete exploit {{.ID}}? Running jobs may be stopped; new jobs will not be scheduled.');"><input type="hidden" name="id" value="{{.ID}}"><input type="hidden" name="action" value="delete"><button type="submit" class="warn">Delete</button></form>
            </div>
          </td>
        </tr>
      {{else}}
        <tr><td class="empty" colspan="6">No exploits uploaded.</td></tr>
      {{end}}
      </tbody>
    </table>
    </div>
  </section>

  <section class="card table-card" id="workers">
    <div class="table-top"><h2>Workers</h2></div>
    <div class="table-wrap">
    <table>
      <thead><tr><th>Client</th><th>Targets</th></tr></thead>
      <tbody>
      {{range .Buckets}}
        <tr>
          <td><code>{{.Client}}</code></td>
          <td>{{range .Teams}}<div>{{.Name}}: <code>{{.IP}}</code></div>{{end}}</td>
        </tr>
      {{else}}
        <tr><td class="empty" colspan="2">No active workers have claimed targets yet.</td></tr>
      {{end}}
      </tbody>
    </table>
    </div>
  </section>
</main>
</body>
</html>
{{end}}

{{define "logs"}}
{{template "baseHead" .}}
  <p><a class="button secondary" href="/">Back</a></p>
  <h2>Logs for {{.ID}} v{{.Version}}</h2>
  {{if .Error}}<div class="err">{{.Error}}</div>{{end}}
  <div class="card">
    {{range .Lines}}
      <pre>[{{.Level}}] {{.Team}} {{.Message}}</pre>
    {{else}}
      <p>No logs found.</p>
    {{end}}
  </div>
</main>
</body>
</html>
{{end}}
`
