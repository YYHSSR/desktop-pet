#include "PetFrameItem.hpp"
#include "PetWindowController.hpp"

#include <QPainter>

namespace Pet::UI {

PetFrameItem::PetFrameItem(QQuickItem* parent)
    : QQuickPaintedItem(parent) {
    setAntialiasing(false);
    setMipmap(false);
    setOpaquePainting(false);
    setPerformanceHint(QQuickPaintedItem::FastFBOResizing, true);
}

void PetFrameItem::setController(PetWindowController* controller) {
    if (m_controller == controller) {
        return;
    }
    if (m_frameConnection) {
        disconnect(m_frameConnection);
    }
    m_controller = controller;
    if (m_controller) {
        m_frameConnection = connect(m_controller, &PetWindowController::frameIdChanged,
                                    this, [this]() { update(); },
                                    Qt::QueuedConnection);
    }
    emit controllerChanged();
    update();
}

void PetFrameItem::paint(QPainter* painter) {
    if (!m_controller) {
        return;
    }
    const QImage image = m_controller->currentImage();
    if (image.isNull()) {
        return;
    }
    painter->setRenderHint(QPainter::SmoothPixmapTransform, true);
    painter->drawImage(boundingRect(), image);
}

} // namespace Pet::UI
