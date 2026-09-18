#pragma once

#include <QObject>
#include <QString>
#include <QImage>

namespace Pet::Media {

class AnimationClip : public QObject {
    Q_OBJECT
public:
    explicit AnimationClip(QObject* parent = nullptr) : QObject(parent) {}
    ~AnimationClip() override = default;

    virtual void start() = 0;
    virtual void stop() = 0;
    virtual void jumpToFrame(int frameIndex) = 0;
    virtual void setPlaybackSpeed(double speed) = 0;

    [[nodiscard]] virtual int frameCount() const = 0;
    [[nodiscard]] virtual double durationSeconds() const = 0;
    [[nodiscard]] virtual int currentFrameIndex() const = 0;
    [[nodiscard]] virtual QImage currentImage() const = 0;

signals:
    void frameChanged(int frameIndex);
    void finished();
    void errorOccurred(const QString& error);
};

} // namespace Pet::Media
