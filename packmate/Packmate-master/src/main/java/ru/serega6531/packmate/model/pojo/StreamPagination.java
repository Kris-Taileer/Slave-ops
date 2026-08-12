package ru.serega6531.packmate.model.pojo;

import lombok.Data;
import org.jetbrains.annotations.Nullable;
import ru.serega6531.packmate.model.Pattern;

@Data
public class StreamPagination {

    @Nullable
    private Long startingFrom;

    private int pageSize;

    private boolean favorites; // определяет, искать только избранные стримы или все

    @Nullable
    private Pattern pattern; // если не null, ищем стримы с этим паттерном

}
