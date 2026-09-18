cmake_minimum_required(VERSION 3.20)
project(DesktopPetCpp
    VERSION 1.0.0
    LANGUAGES CXX
)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Ensure strict UTF-8 string encoding across GCC, Clang and MSVC
if(MSVC)
    add_compile_options(/utf-8)
else()
    add_compile_options(-finput-charset=UTF-8 -fexec-charset=UTF-8)
endif()

# Enable Qt MOC, UIC and RCC automation
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTOMOC_PATH_PREFIX OFF)

# Find Qt 6 Packages
find_package(Qt6 REQUIRED COMPONENTS
    Core
    Gui
    Quick
    Qml
    Network
    Widgets
    Sql
    Concurrent
)

if(COMMAND qt_policy)
    qt_policy(SET QTP0001 NEW)
    # QML 文件全部位于单一 qml/ 目录（无额外子目录）：NEW 策略下零警告
    qt_policy(SET QTP0004 NEW)
endif()

# Output directories
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)

# Include directories
include_directories(
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}/src/core
    ${CMAKE_CURRENT_SOURCE_DIR}/src/infrastructure
    ${CMAKE_CURRENT_SOURCE_DIR}/src/media
    ${CMAKE_CURRENT_SOURCE_DIR}/src/services
    ${CMAKE_CURRENT_SOURCE_DIR}/src/services/agent
    ${CMAKE_CURRENT_SOURCE_DIR}/src/services/bridge
    ${CMAKE_CURRENT_SOURCE_DIR}/src/services/behavior
    ${CMAKE_CURRENT_SOURCE_DIR}/src/ui
    ${CMAKE_CURRENT_SOURCE_DIR}/src/ui/context_menus
    ${CMAKE_CURRENT_SOURCE_DIR}/src/application
)

# Core library (Pure math & algorithms, zero GUI dependency)
add_library(pet_core STATIC
    src/core/Geometry.hpp
    src/core/PetBody.hpp
)
target_link_libraries(pet_core PUBLIC Qt6::Core)

# Infrastructure library (IPC, native Win32, config)
add_library(pet_infrastructure STATIC
    src/infrastructure/PathHelper.hpp
    src/infrastructure/PathHelper.cpp
    src/infrastructure/ConfigManager.hpp
    src/infrastructure/ConfigManager.cpp
    src/infrastructure/NativeWindowHelper.hpp
    src/infrastructure/NativeWindowHelper.cpp
)
target_link_libraries(pet_infrastructure PUBLIC pet_core Qt6::Core Qt6::Network Qt6::Gui)
if(WIN32)
    target_link_libraries(pet_infrastructure PUBLIC user32 dwmapi shell32)
endif()

# Media library (character library, animation)
add_library(pet_media STATIC
    src/media/AnimationClip.hpp
    src/media/CharacterCatalog.hpp
    src/media/CharacterCatalog.cpp
    src/media/MusicDetector.hpp
    src/media/MusicDetector.cpp
    src/media/WebMPlayer.hpp
    src/media/WebMPlayer.cpp
    src/media/AnimationThumbnail.hpp
    src/media/AnimationThumbnail.cpp
)
target_link_libraries(pet_media PUBLIC pet_core pet_infrastructure Qt6::Core Qt6::Gui)
if(WIN32)
    # MusicDetector 用的 WASAPI GUID 定义（CLSID_MMDeviceEnumerator 等）
    target_link_libraries(pet_media PUBLIC uuid)
endif()

add_library(pet_services STATIC
    src/services/bridge/ProtocolMessage.hpp
    src/services/bridge/ProtocolMessage.cpp
    src/services/bridge/ProtocolCodec.hpp
    src/services/bridge/ProtocolCodec.cpp
    src/services/bridge/PythonBridge.hpp
    src/services/bridge/PythonBridge.cpp
    src/services/bridge/PythonServiceSupervisor.hpp
    src/services/bridge/PythonServiceSupervisor.cpp
    src/services/bridge/JobObjectGuard.hpp
    src/services/bridge/JobObjectGuard.cpp
    src/services/behavior/ActionArbiter.hpp
    src/services/behavior/ActionArbiter.cpp
)
target_link_libraries(pet_services PUBLIC
    pet_core pet_infrastructure pet_media
    Qt6::Core Qt6::Network Qt6::Sql Qt6::Concurrent)

# UI & Controller library (QML bridge, controller)
add_library(pet_ui STATIC
    src/ui/PetWindowController.hpp
    src/ui/PetWindowController.cpp
    src/ui/PetFrameItem.hpp
    src/ui/PetFrameItem.cpp
    src/ui/SystemTrayBridge.hpp
    src/ui/SystemTrayBridge.cpp
    src/ui/ModernContextMenu.hpp
    src/ui/ModernContextMenu.cpp
    src/ui/context_menus/MenuStyle.hpp
    src/ui/context_menus/MenuStyle.cpp
    src/ui/context_menus/MenuIcons.hpp
    src/ui/context_menus/MenuIcons.cpp
    src/ui/context_menus/MenuIconProvider.hpp
    src/ui/context_menus/MenuPositioning.hpp
    src/ui/context_menus/MenuPositioning.cpp
    src/ui/context_menus/MenuPrimitives.hpp
    src/ui/context_menus/MenuPrimitives.cpp
    src/ui/context_menus/MenuExtras.hpp
    src/ui/context_menus/MenuExtras.cpp
    src/ui/FunImagePopup.hpp
    src/ui/FunImagePopup.cpp
    src/ui/ClickTalkBindingsDialog.hpp
    src/ui/ClickTalkBindingsDialog.cpp
)
target_link_libraries(pet_ui PUBLIC pet_core pet_infrastructure pet_media pet_services Qt6::Core Qt6::Gui Qt6::Quick Qt6::Qml Qt6::Widgets)
if(WIN32)
    target_link_libraries(pet_ui PUBLIC user32 dwmapi)
endif()

# Application library
add_library(pet_application STATIC
    src/application/PetApplication.hpp
    src/application/PetApplication.cpp
)
target_link_libraries(pet_application PUBLIC pet_ui)

# Main Executable
if(WIN32)
    # assets/app.rc 把 icon.ico 嵌入 exe（资源管理器/任务栏图标，与 Python 版一致）
    add_executable(desktop-pet WIN32 src/main.cpp assets/app.rc)
else()
    add_executable(desktop-pet src/main.cpp)
endif()

target_link_libraries(desktop-pet PRIVATE
    pet_application
    Qt6::Core
    Qt6::Gui
    Qt6::Quick
    Qt6::Qml
    Qt6::Widgets
)

# Embed QML resources
qt_add_qml_module(desktop-pet
    URI "DesktopPet"
    VERSION 1.0
    QML_FILES
        qml/MainPetWindow.qml
        qml/SpeechBubble.qml
        qml/SpringDragArea.qml
        qml/ModernSettingsView.qml
        qml/SettingsCard.qml
        qml/SettingsToggleRow.qml
        qml/SettingsSliderRow.qml
        qml/SettingsChoiceRow.qml
        qml/SettingsToggle.qml
        qml/SettingsTextRow.qml
        qml/SettingsTextAreaRow.qml
        qml/SettingsPathRow.qml
        qml/SettingsColorRow.qml
        qml/SettingsButtonRow.qml
        qml/SettingsFontRow.qml
)

# Optional unit tests
enable_testing()
add_subdirectory(tests)
