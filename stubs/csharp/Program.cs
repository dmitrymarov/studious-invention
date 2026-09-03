// Заглушка сервиса на C# для тестовой среды мониторинга.
// Контракт тот же, что у python-заглушки: /health, /metrics, /chaos/{ok,degrade,down}.
using System.Globalization;
using System.Text;

var service = Environment.GetEnvironmentVariable("SERVICE_NAME") ?? "csharp-service";
var group = Environment.GetEnvironmentVariable("SERVICE_GROUP") ?? "apps";
var role = Environment.GetEnvironmentVariable("SERVICE_ROLE") ?? "generic";
var port = Environment.GetEnvironmentVariable("PORT") ?? "8000";
var warmup = double.Parse(
    Environment.GetEnvironmentVariable("WARMUP_SECONDS") ?? "3",
    CultureInfo.InvariantCulture);

var started = DateTime.UtcNow;
var state = new State();

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls($"http://0.0.0.0:{port}");
builder.Logging.ClearProviders();
var app = builder.Build();

// Фоновая активность, чтобы счётчики росли и rate() было что показывать.
_ = Task.Run(async () =>
{
    var rnd = new Random();
    while (true)
    {
        await Task.Delay(rnd.Next(500, 2000));
        lock (state)
        {
            state.Jobs += rnd.Next(1, 5);
            if (state.Mode == "degraded") state.Errors += rnd.Next(1, 4);
        }
    }
});

bool Ready() => (DateTime.UtcNow - started).TotalSeconds >= warmup;

app.Use(async (ctx, next) =>
{
    lock (state) state.Requests++;
    await next();
});

app.MapGet("/metrics", () =>
{
    string mode; long requests, errors, jobs;
    lock (state) { mode = state.Mode; requests = state.Requests; errors = state.Errors; jobs = state.Jobs; }

    var labels = $"service=\"{service}\",group=\"{group}\",role=\"{role}\"";
    var up = (mode == "down" || !Ready()) ? 0 : 1;
    var degraded = mode == "degraded" ? 1 : 0;
    var uptime = (DateTime.UtcNow - started).TotalSeconds;

    var sb = new StringBuilder();
    sb.Append("# HELP app_up Сервис считает себя работоспособным.\n# TYPE app_up gauge\n");
    sb.Append(CultureInfo.InvariantCulture, $"app_up{{{labels}}} {up}\n");
    sb.Append("# HELP app_degraded Сервис жив, но работает деградированно.\n# TYPE app_degraded gauge\n");
    sb.Append(CultureInfo.InvariantCulture, $"app_degraded{{{labels}}} {degraded}\n");
    sb.Append("# HELP app_uptime_seconds Время с момента старта процесса.\n# TYPE app_uptime_seconds gauge\n");
    sb.Append(CultureInfo.InvariantCulture, $"app_uptime_seconds{{{labels}}} {uptime:F1}\n");
    sb.Append("# HELP app_requests_total Обработано HTTP-запросов.\n# TYPE app_requests_total counter\n");
    sb.Append(CultureInfo.InvariantCulture, $"app_requests_total{{{labels}}} {requests}\n");
    sb.Append("# HELP app_errors_total Ошибок обработки.\n# TYPE app_errors_total counter\n");
    sb.Append(CultureInfo.InvariantCulture, $"app_errors_total{{{labels}}} {errors}\n");
    sb.Append("# HELP app_jobs_total Обработано фоновых задач.\n# TYPE app_jobs_total counter\n");
    sb.Append(CultureInfo.InvariantCulture, $"app_jobs_total{{{labels}}} {jobs}\n");

    return Results.Text(sb.ToString(), "text/plain; charset=utf-8");
});

IResult Health()
{
    string mode;
    lock (state) mode = state.Mode;
    if (mode == "down") return Results.Json(new { status = "down", service }, statusCode: 503);
    if (!Ready()) return Results.Json(new { status = "starting", service }, statusCode: 503);
    return Results.Json(new { status = mode == "degraded" ? "degraded" : "ok", service });
}

app.MapGet("/health", Health);
app.MapGet("/healthz", Health);

app.MapGet("/chaos/{want}", (string want) =>
{
    if (want is not ("ok" or "degrade" or "down"))
        return Results.Json(new { error = "ok|degrade|down" }, statusCode: 400);
    lock (state) state.Mode = want == "degrade" ? "degraded" : want;
    return Results.Json(new { service, mode = state.Mode });
});

app.MapGet("/", () => Results.Json(new { service, group, role, mode = state.Mode }));

app.Run();

internal sealed class State
{
    public string Mode = "ok";
    public long Requests;
    public long Errors;
    public long Jobs;
}
