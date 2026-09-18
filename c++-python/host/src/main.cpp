#include "application/PetApplication.hpp"
#include <QFile>
#include <QTextStream>
#include <QDateTime>
#include <QDir>
#include <QMutex>
#include <QMutexLocker>

void customLog(QtMsgType type, const QMessageLogContext &context, const QString &msg) {
    Q_UNUSED(type);
    Q_UNUSED(context);
    static QFile logFile;
    static QMutex mutex;
    const QMutexLocker locker(&mutex);
    if (!logFile.isOpen()) {
        const QString base = qEnvironmentVariable("APPDATA", QDir::tempPath());
        const QString logDir = QDir(base).filePath(QStringLiteral("desktop-pet"));
        QDir().mkpath(logDir);
        const QString logPath = QDir(logDir).filePath(
            QStringLiteral("pet-cpp-%1.log").arg(QCoreApplication::applicationPid()));
        logFile.setFileName(logPath);
        if (!logFile.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
            return;
        }
    }
    QTextStream out(&logFile);
    out << QDateTime::currentDateTime().toString("yyyy-MM-dd hh:mm:ss.zzz ")
        << msg << "\n";
    out.flush();
}

int main(int argc, char* argv[]) {
    qInstallMessageHandler(customLog);
    
    // 启用高分屏自动缩放支持
    QGuiApplication::setHighDpiScaleFactorRoundingPolicy(
        Qt::HighDpiScaleFactorRoundingPolicy::PassThrough
    );

    Pet::Application::PetApplication app(argc, argv);
    return app.exec();
}
