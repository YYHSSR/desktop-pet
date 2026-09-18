#include "WebMPlayer.hpp"
#include "infrastructure/PathHelper.hpp"
#include <QPainter>
#include <QFileInfo>
#include <QDebug>
#include <algorithm>
#include <cmath>

namespace Pet::Media {

constexpr int FRAME_W = 640;
constexpr int FRAME_H = 360;
constexpr int BYTES_PER_FRAME = FRAME_W * FRAME_H * 4;

WebMPlayer::WebMPlayer(const QString& mediaPath, QObject* parent)
    : AnimationClip(parent)
    , m_mediaPath(mediaPath)
    , m_timer(new QTimer(this))
    , m_process(new QProcess(this)) {

    connect(m_timer, &QTimer::timeout, this, &WebMPlayer::onTimerTick);
    m_timer->setTimerType(Qt::PreciseTimer);
    m_timer->setInterval(42); // 素材默认 24 FPS

    connect(m_process, &QProcess::readyReadStandardOutput, this, &WebMPlayer::onProcessReadyRead);
    connect(m_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &WebMPlayer::onProcessFinished);

    generateFallbackFrame();
}

WebMPlayer::~WebMPlayer() {
    stop();
}

void WebMPlayer::load(const QString& mediaPath, bool loop) {
    m_loop = loop;
    m_mediaPath = mediaPath;
    start();
}

void WebMPlayer::start() {
    m_timer->stop();
    if (m_process->state() != QProcess::NotRunning) {
        m_process->kill();
        m_process->waitForFinished(200);
    }
    m_readBuffer.clear();
    m_frameQueue.clear();
    m_currentFrameIndex = 0;
    launchDecoder();
    if (!m_timer->isActive()) {
        m_timer->start();
    }
}

void WebMPlayer::stop() {
    m_timer->stop();
    if (m_process->state() != QProcess::NotRunning) {
        m_process->kill();
        m_process->waitForFinished(200);
    }
}

void WebMPlayer::jumpToFrame(int frameIndex) {
    m_currentFrameIndex = frameIndex;
}

void WebMPlayer::setPlaybackSpeed(double speed) {
    const double nextSpeed = std::clamp(speed, 0.1, 8.0);
    if (std::abs(m_speed - nextSpeed) < 1e-4) {
        return;
    }
    m_speed = nextSpeed;
    m_timer->setInterval(std::max(4, static_cast<int>(std::lround(1000.0 / (24.0 * m_speed)))));
    // FFmpeg is input-paced as well as the presentation timer.  Restarting
    // here keeps 0.5x/2x playback from starving or flooding the frame FIFO.
    if (m_process->state() != QProcess::NotRunning) {
        start();
    }
}

void WebMPlayer::launchDecoder() {
    if (m_process->state() != QProcess::NotRunning) {
        m_process->kill();
        m_process->waitForFinished(100);
    }

    qDebug() << "launchDecoder mediaPath:" << m_mediaPath;
    if (m_mediaPath.isEmpty() || !QFileInfo::exists(m_mediaPath)) {
        qWarning() << "mediaPath does not exist:" << m_mediaPath;
        return;
    }

    QString ffmpeg = Infrastructure::PathHelper::getFfmpegPath();
    qDebug() << "Using ffmpeg:" << ffmpeg;
    QStringList args = {
        "-nostdin",
        "-loglevel", "quiet",
        "-readrate", QString::number(m_speed, 'f', 3),
        "-c:v", "libvpx-vp9",
        "-i", m_mediaPath,
        "-an", "-sn", "-dn",
        "-f", "rawvideo",
        "-pix_fmt", "rgba",
        "-s", QString("%1x%2").arg(FRAME_W).arg(FRAME_H),
        "-"
    };

    m_process->start(ffmpeg, args);
}

void WebMPlayer::onProcessReadyRead() {
    m_readBuffer.append(m_process->readAllStandardOutput());
    drainDecodedFrames();
}

void WebMPlayer::drainDecodedFrames() {
    qsizetype consumed = 0;
    while (m_frameQueue.size() < MAX_QUEUED_FRAMES
           && m_readBuffer.size() - consumed >= BYTES_PER_FRAME) {
        QImage frame(reinterpret_cast<const uchar*>(m_readBuffer.constData() + consumed),
                     FRAME_W, FRAME_H, FRAME_W * 4, QImage::Format_RGBA8888);
        m_frameQueue.enqueue(frame.copy());
        consumed += BYTES_PER_FRAME;

        if (m_currentImage.isNull() && !m_frameQueue.isEmpty()) {
            m_currentImage = m_frameQueue.head();
            emit frameChanged(0);
        }

    }
    if (consumed > 0) {
        m_readBuffer.remove(0, consumed);
    }
}

void WebMPlayer::onTimerTick() {
    if (!m_frameQueue.isEmpty()) {
        m_currentImage = m_frameQueue.dequeue();
        // Frames already copied into the byte buffer may not produce another
        // readyRead signal.  Refill the FIFO after each presentation tick.
        drainDecodedFrames();
        m_currentFrameIndex++;
        emit frameChanged(m_currentFrameIndex);
    } else if (m_process->state() == QProcess::NotRunning) {
        if (m_loop && !m_mediaPath.isEmpty() && QFileInfo::exists(m_mediaPath)) {
            launchDecoder();
        } else {
            m_timer->stop();
            QTimer::singleShot(0, this, [this]() {
                emit finished();
            });
        }
    }
}

void WebMPlayer::onProcessFinished(int exitCode, QProcess::ExitStatus status) {
    Q_UNUSED(exitCode);
    Q_UNUSED(status);
}

void WebMPlayer::generateFallbackFrame() {
    QImage img(FRAME_W, FRAME_H, QImage::Format_RGBA8888);
    img.fill(Qt::transparent);
    QPainter p(&img);
    p.setRenderHint(QPainter::Antialiasing);
    p.setBrush(QColor(255, 182, 193, 230));
    p.setPen(QColor(255, 105, 180));
    p.drawEllipse(FRAME_W / 2 - 90, FRAME_H / 2 - 90, 180, 180);
    p.setPen(Qt::white);
    QFont f = p.font();
    f.setPointSize(18);
    f.setBold(true);
    p.setFont(f);
    p.drawText(QRect(0, 0, FRAME_W, FRAME_H), Qt::AlignCenter, "ShenShen");
    m_currentImage = img;
}

} // namespace Pet::Media
