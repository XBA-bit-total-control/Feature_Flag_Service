# Демонстрация A/B-эксперимента

Создадим флаг и два варианта, распределим учебных пользователей через `/resolve`,
отправим конверсии, пересчитаем результаты и прочитаем шаблонный отчёт.
Сценарий работает без ключа LLM.

Нужны запущенный API и аккаунт администратора из
[руководства по запуску](getting-started.md#первый-администратор). Выполняйте
блоки по порядку в одном PowerShell 7. Если API опубликован на другом порту,
измените `$api`. Если зарегистрировали другой email, измените `$email`.
У каждого прогона свои ключ флага и имена пользователей.

## 1. Войти и сохранить токен

```powershell
$api = 'http://127.0.0.1:8000'
$email = 'admin@example.com'
$password = Read-Host 'Пароль администратора' -MaskInput
$login = Invoke-RestMethod -Method Post -Uri "$api/auth/login" -ContentType 'application/json' -Body (@{
    email = $email
    password = $password
} | ConvertTo-Json)
$auth = @{ Authorization = "Bearer $($login.access_token)" }

function Wait-DemoResource {
    param([string]$Uri, [scriptblock]$Ready = { $true })

    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $data = Invoke-RestMethod -Uri $Uri -Headers $auth -SkipHttpErrorCheck -StatusCodeVariable code
        if ($code -eq 200 -and (& $Ready $data)) { return }
        if ($code -notin @(200, 404)) { throw "GET $Uri вернул HTTP $code" }
        Start-Sleep -Milliseconds 150
    }
    throw "Ресурс ещё недоступен: $Uri"
}
```

`POST /auth/login` возвращает `access_token`. Заголовок `$auth` используется
для управления флагом, экспериментом и отчётом.
`Wait-DemoResource` дожидается, пока изменение станет видно в следующем
запросе к PostgreSQL. Это важно при последовательном запуске блоков без пауз.

## 2. Создать флаг и варианты

```powershell
$run = [guid]::NewGuid().ToString('N').Substring(0, 8)
$flagKey = "checkout-demo-$run"
$flag = Invoke-RestMethod -Method Post -Uri "$api/api/flags" -Headers $auth -ContentType 'application/json' -Body (@{
    key = $flagKey
    type = 'ab'
    enabled = $true
} | ConvertTo-Json)
Wait-DemoResource "$api/api/flags/$flagKey"

foreach ($key in @('A', 'B')) {
    Invoke-RestMethod -Method Post -Uri "$api/api/flags/$flagKey/variants" -Headers $auth -ContentType 'application/json' -Body (@{
        flag_id = $flag.id
        variant_key = $key
        weight = 1
    } | ConvertTo-Json) | Out-Null
}
Wait-DemoResource "$api/api/flags/$flagKey/variants" { param($items) $items.Count -eq 2 }
```

`$flagKey` нужен в маршрутах вариантов и в событиях. `$flag.id` — UUID флага,
который передаётся в `flag_id`. Вариант `A` используется как контрольный,
`B` — как тестовый. Равные веса дают одинаковую долю назначений в среднем.

## 3. Создать и запустить эксперимент

```powershell
$experiment = Invoke-RestMethod -Method Post -Uri "$api/api/experiments" -Headers $auth -ContentType 'application/json' -Body (@{
    flag_id = $flag.id
    name = "Checkout demo $run"
    primary_metric = 'purchase'
    min_sample_size = 50
} | ConvertTo-Json)
$experimentId = $experiment.id
$experimentUrl = "$api/api/experiments/$experimentId"
Wait-DemoResource $experimentUrl

$started = Invoke-RestMethod -Method Patch -Uri $experimentUrl -Headers $auth -ContentType 'application/json' -Body '{"status":"running"}'
Wait-DemoResource $experimentUrl { param($item) $item.status -eq 'running' }
$started | Select-Object id, status, start_date
```

Новый эксперимент сначала находится в `draft`. После `PATCH` ожидаются
`status: running` и заполненный `start_date`. `$experimentId` потребуется
в событиях и в URL расчёта и отчёта. Метрика `purchase` должна совпадать с
`event_name` у конверсий.

## 4. Вызвать `/resolve` и записать показы

`POST /resolve` для включённого A/B-флага возвращает назначенный вариант
и сразу записывает показ. Поэтому блок отправляет по одному запросу на
пользователя и сохраняет `user_id` вместе с полученным вариантом. Он набирает
минимум 160 пользователей и не менее 50 в каждой группе.

```powershell
$groups = @{ A = @(); B = @() }
for ($i = 1; $i -le 400; $i++) {
    $userId = "demo-$run-$i"
    $assignment = Invoke-RestMethod -Method Post -Uri "$api/resolve" -ContentType 'application/json' -Body (@{
        flag_key = $flagKey
        user_id = $userId
    } | ConvertTo-Json)
    if (-not $assignment.enabled -or $assignment.experiment_id -ne $experimentId -or $assignment.variant -notin @('A', 'B')) {
        throw "Не получено назначение для $userId"
    }
    $groups[$assignment.variant] += $assignment.user_id
    if ($i -ge 160 -and $groups.A.Count -ge 50 -and $groups.B.Count -ge 50) { break }
}
if ($groups.A.Count -lt 50 -or $groups.B.Count -lt 50) {
    throw 'Демонстрация не набрала 50 пользователей в каждом варианте'
}
"Показы A: $($groups.A.Count), B: $($groups.B.Count)"
```

Типичный ответ `/resolve` содержит `enabled: true`, `reason: ab_test`,
`variant: A` или `B` и UUID эксперимента в `experiment_id`. Размеры групп могут отличаться между
прогонами, поскольку назначение зависит от идентификатора эксперимента и пользователя.

## 5. Отправить конверсии

Отправим `purchase` примерно для 10% группы `A` и 30% группы `B`. Используем
те же `user_id` и назначенные `variant_key`. Сервер проставит время события
после сохранения показа, если поле `timestamp` опущено.

```powershell
$rates = @{ A = 0.10; B = 0.30 }
$expected = @{}
$events = @(foreach ($key in @('A', 'B')) {
    $expected[$key] = [int][Math]::Floor($groups[$key].Count * $rates[$key])
    foreach ($userId in ($groups[$key] | Select-Object -First $expected[$key])) {
        @{
            event_type = 'conversion'
            event_name = 'purchase'
            flag_key = $flagKey
            experiment_id = $experimentId
            variant_key = $key
            user_id = $userId
        }
    }
})

for ($offset = 0; $offset -lt $events.Count; $offset += 100) {
    $batch = @($events | Select-Object -Skip $offset -First 100)
    $receipt = Invoke-RestMethod -Method Post -Uri "$api/api/events" -ContentType 'application/json' -Body (@{
        events = $batch
    } | ConvertTo-Json -Depth 4)
    if ($receipt.accepted -ne $batch.Count -or $receipt.rejected -ne 0) {
        throw "Принято $($receipt.accepted) из $($batch.Count) конверсий"
    }
    $receipt
}
```

Для каждого запроса ожидается HTTP `201`, число `accepted` равно размеру
пакета, `rejected` равно `0`. Отдельно отправлять события `impression` не нужно.

## 6. Пересчитать результаты и получить отчёт

Ручной пересчёт читает уже сохранённые события. Ждать обработки воркером
или планового запуска Beat для этого шага не нужно.

```powershell
$rows = Invoke-RestMethod -Method Post -Uri "$experimentUrl/results" -Headers $auth
if ($rows.Count -ne 2) { throw "Ожидались две строки результатов, получено $($rows.Count)" }
foreach ($row in $rows) {
    $key = $row.variant_name
    if ($row.sample_size -ne $groups[$key].Count -or $row.conversions -ne $expected[$key]) {
        throw "Счётчики варианта $key не совпали с отправленными данными"
    }
}
$rows | Select-Object variant_name, sample_size, conversions, conversion_rate, p_value, enough_data, is_significant

Wait-DemoResource "$experimentUrl/results" { param($items) $items.Count -eq 2 }
$report = Invoke-RestMethod -Uri "$experimentUrl/template/reports" -Headers $auth
$report.report_type
$report.report_content
```

В `results` ожидаются две строки. `sample_size` совпадает с числом назначений,
`conversions` — с числом отправленных конверсий для варианта. В одном
проверочном прогоне получились такие значения.

| Вариант | Участников | Конверсий | `conversion_rate` |
|---------|-----------:|----------:|------------------:|
| `A`     |         84 |         8 |            0,0952 |
| `B`     |         76 |        22 |            0,2895 |

Ожидаются `enough_data: true`, `is_significant: true` и `p_value < 0.05`.
В проверочном прогоне `p_value` было около 0,00167.
Это проверка на синтетических данных. `GET …/template/reports` сразу возвращает
`report_type: template` и текст со счётчиками, конверсиями и выводом о более
высокой конверсии тестового варианта.

После проверки можно остановить эксперимент, сохранив его результаты.

```powershell
Invoke-RestMethod -Method Patch -Uri $experimentUrl -Headers $auth -ContentType 'application/json' -Body '{"status":"stopped"}' |
    Select-Object id, status, end_date
Wait-DemoResource $experimentUrl { param($item) $item.status -eq 'stopped' }
```
