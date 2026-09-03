// Заглушка сервиса на C++ для тестовой среды мониторинга.
// Контракт тот же, что у python- и csharp-заглушек:
// /health, /metrics, /chaos/{ok,degrade,down}.
//
// Голые POSIX-сокеты вместо http-библиотеки: образ собирается статически
// под musl и весит пару мегабайт, а нам нужен ровно один GET-обработчик.
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <random>
#include <sstream>
#include <string>
#include <thread>

namespace {

std::string env(const char* key, const char* fallback) {
    const char* v = std::getenv(key);
    return v ? std::string(v) : std::string(fallback);
}

const std::string kService = env("SERVICE_NAME", "cpp-service");
const std::string kGroup = env("SERVICE_GROUP", "apps");
const std::string kRole = env("SERVICE_ROLE", "generic");
const double kWarmup = std::stod(env("WARMUP_SECONDS", "3"));

std::atomic<int> g_mode{0};  // 0 = ok, 1 = degraded, 2 = down
std::atomic<long> g_requests{0};
std::atomic<long> g_errors{0};
std::atomic<long> g_jobs{0};
const auto g_started = std::chrono::steady_clock::now();

double uptime() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - g_started).count();
}

bool ready() { return uptime() >= kWarmup; }

// Фоновая активность, чтобы счётчики росли и rate() было что показывать.
void worker() {
    std::mt19937 rng{std::random_device{}()};
    std::uniform_int_distribution<int> sleep_ms(500, 2000);
    std::uniform_int_distribution<int> step(1, 4);
    for (;;) {
        std::this_thread::sleep_for(std::chrono::milliseconds(sleep_ms(rng)));
        g_jobs += step(rng);
        if (g_mode.load() == 1) g_errors += step(rng);
    }
}

std::string metrics() {
    const std::string labels =
        "service=\"" + kService + "\",group=\"" + kGroup + "\",role=\"" + kRole + "\"";
    const int mode = g_mode.load();
    const int up = (mode == 2 || !ready()) ? 0 : 1;
    const int degraded = mode == 1 ? 1 : 0;

    std::ostringstream o;
    o.setf(std::ios::fixed);
    o.precision(1);
    o << "# HELP app_up Сервис считает себя работоспособным.\n# TYPE app_up gauge\n"
      << "app_up{" << labels << "} " << up << "\n"
      << "# HELP app_degraded Сервис жив, но работает деградированно.\n# TYPE app_degraded gauge\n"
      << "app_degraded{" << labels << "} " << degraded << "\n"
      << "# HELP app_uptime_seconds Время с момента старта процесса.\n# TYPE app_uptime_seconds gauge\n"
      << "app_uptime_seconds{" << labels << "} " << uptime() << "\n"
      << "# HELP app_requests_total Обработано HTTP-запросов.\n# TYPE app_requests_total counter\n"
      << "app_requests_total{" << labels << "} " << g_requests.load() << "\n"
      << "# HELP app_errors_total Ошибок обработки.\n# TYPE app_errors_total counter\n"
      << "app_errors_total{" << labels << "} " << g_errors.load() << "\n"
      << "# HELP app_jobs_total Обработано фоновых задач.\n# TYPE app_jobs_total counter\n"
      << "app_jobs_total{" << labels << "} " << g_jobs.load() << "\n";
    return o.str();
}

std::string respond(int code, const std::string& status, const std::string& body,
                    const char* ctype) {
    std::ostringstream o;
    o << "HTTP/1.1 " << code << " " << status << "\r\n"
      << "Content-Type: " << ctype << "\r\n"
      << "Content-Length: " << body.size() << "\r\n"
      << "Connection: close\r\n\r\n"
      << body;
    return o.str();
}

std::string handle(const std::string& path) {
    const char* kJson = "application/json";
    const char* kText = "text/plain; charset=utf-8";

    if (path == "/metrics") return respond(200, "OK", metrics(), kText);

    if (path == "/health" || path == "/healthz") {
        const int mode = g_mode.load();
        if (mode == 2)
            return respond(503, "Service Unavailable",
                           "{\"status\":\"down\",\"service\":\"" + kService + "\"}", kJson);
        if (!ready())
            return respond(503, "Service Unavailable",
                           "{\"status\":\"starting\",\"service\":\"" + kService + "\"}", kJson);
        const std::string st = mode == 1 ? "degraded" : "ok";
        return respond(200, "OK", "{\"status\":\"" + st + "\",\"service\":\"" + kService + "\"}",
                       kJson);
    }

    if (path.rfind("/chaos/", 0) == 0) {
        const std::string want = path.substr(7);
        if (want == "ok") g_mode = 0;
        else if (want == "degrade") g_mode = 1;
        else if (want == "down") g_mode = 2;
        else return respond(400, "Bad Request", "{\"error\":\"ok|degrade|down\"}", kJson);
        const char* names[] = {"ok", "degraded", "down"};
        return respond(200, "OK",
                       "{\"service\":\"" + kService + "\",\"mode\":\"" +
                           names[g_mode.load()] + "\"}",
                       kJson);
    }

    if (path == "/" || path.empty())
        return respond(200, "OK",
                       "{\"service\":\"" + kService + "\",\"group\":\"" + kGroup +
                           "\",\"role\":\"" + kRole + "\"}",
                       kJson);

    return respond(404, "Not Found", "{\"error\":\"not found\"}", kJson);
}

void serve(int fd) {
    char buf[4096];
    const ssize_t n = ::recv(fd, buf, sizeof(buf) - 1, 0);
    if (n > 0) {
        buf[n] = '\0';
        // Разбираем только "GET <path> HTTP/1.x" - большего заглушке не нужно.
        std::string path = "/";
        std::istringstream req{std::string(buf, static_cast<size_t>(n))};
        std::string method, target;
        if (req >> method >> target) {
            const size_t q = target.find('?');
            path = q == std::string::npos ? target : target.substr(0, q);
            if (path.size() > 1 && path.back() == '/') path.pop_back();
        }
        ++g_requests;
        const std::string out = handle(path);
        ::send(fd, out.data(), out.size(), MSG_NOSIGNAL);
    }
    ::close(fd);
}

}  // namespace

int main() {
    const int port = std::stoi(env("PORT", "8000"));

    const int srv = ::socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) return 1;
    int one = 1;
    ::setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(static_cast<uint16_t>(port));
    if (::bind(srv, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) return 1;
    if (::listen(srv, 64) < 0) return 1;

    std::thread(worker).detach();

    for (;;) {
        const int fd = ::accept(srv, nullptr, nullptr);
        if (fd < 0) continue;
        std::thread(serve, fd).detach();
    }
}
