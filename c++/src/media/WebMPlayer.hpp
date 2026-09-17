#pragma once

#include "AnimationClip.hpp"
#include <QTimer>
#include <QProcess>
#include <QQueue>
#include <QByteArray>
#include <QImage>

namespace Pet::Media {

class WebMPlayer : public AnimationClip {
    Q_OBJECT
public:
    explicit WebMPlayer(const QString& mediaPath, QObject* parent = nullptr);
    ~WebMPlayer() override;

    void load(const QString& mediaPath, bool loop = true);
    void start() override;
    void stop() override;
    void jumpToFrame(int frameIndex) override;
    void setPlaybackSpeed(double speed) override;
    void setLoop(bool loop) noexcept { m_loop = loop; }
    [[nodiscard]] bool loop() const noexcept { return m_loop; }

    [[nodiscard]] int frameCount() const override { return m_frameCount; }
    [[nodiscard]] double durationSeconds() const override { return m_durationSeconds; }
    [[nodiscard]] int currentFrameIndex() const override { return m_currentFrameIndex; }
    [[nodiscard]] QImage currentImage() const override { return m_currentImage; }

private slots:
    void onTimerTick();
    void onProcessReadyRead();
    void onProcessFinished(int exitCode, QProcess::ExitStatus status);

private:
    void launchDecoder();
    void drainDecodedFrames();
    void generateFallbackFrame();

    QString m_mediaPath;
    QTimer* m_timer = nullptr;
    QProcess* m_process = nullptr;
    QByteArray m_readBuffer;
    QQueue<QImage> m_frameQueue;
    QImage m_currentImage;

    int m_frameCount = 60;
    int m_currentFrameIndex = 0;
    double m_durationSeconds = 2.5;
    double m_speed = 1.0;
    bool m_loop = true;
    // Match the Python player: a small FIFO absorbs decoder jitter while
    // preserving chronological frames.  Never discard the oldest frame.
    static constexpr int MAX_QUEUED_FRAMES = 8;
};

} // namespace Pet::Media
