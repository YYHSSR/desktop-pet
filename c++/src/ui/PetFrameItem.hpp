#pragma once

#include "PetWindowController.hpp"
#include <QPointer>
#include <QQuickPaintedItem>

namespace Pet::UI {

// Paints the controller's current RGBA frame directly into the scene graph.
// This avoids creating a new image-provider URL and re-running the QML image
// loading pipeline for every video frame.
class PetFrameItem : public QQuickPaintedItem {
    Q_OBJECT
    Q_PROPERTY(Pet::UI::PetWindowController* controller READ controller WRITE setController NOTIFY controllerChanged)

public:
    explicit PetFrameItem(QQuickItem* parent = nullptr);

    PetWindowController* controller() const noexcept { return m_controller; }
    void setController(PetWindowController* controller);
    void paint(QPainter* painter) override;

signals:
    void controllerChanged();

private:
    QPointer<PetWindowController> m_controller;
    QMetaObject::Connection m_frameConnection;
};

} // namespace Pet::UI
