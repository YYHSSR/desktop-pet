add_executable(test_config test_config.cpp)
target_link_libraries(test_config PRIVATE pet_infrastructure Qt6::Core)
add_test(NAME TestConfigCompatibility COMMAND test_config)

add_executable(test_menu_structure test_menu_structure.cpp)
target_link_libraries(test_menu_structure PRIVATE pet_ui Qt6::Widgets)
add_test(NAME TestMenuStructure COMMAND test_menu_structure)

add_executable(test_agent_protocol test_agent_protocol.cpp)
target_link_libraries(test_agent_protocol PRIVATE pet_core Qt6::Core)
add_test(NAME TestAgentProtocol COMMAND test_agent_protocol)

add_executable(test_agent_monitor test_agent_monitor.cpp)
target_link_libraries(test_agent_monitor PRIVATE pet_services Qt6::Core Qt6::Sql)
add_test(NAME TestAgentMonitor COMMAND test_agent_monitor)

# QML 模块导入路径：MinGW 布局为 <prefix>/share/qt6/qml
get_target_property(_qt_qmake Qt6::qmake IMPORTED_LOCATION)
get_filename_component(_qt_bin_dir "${_qt_qmake}" DIRECTORY)
get_filename_component(_qt_prefix "${_qt_bin_dir}" DIRECTORY)
set(PET_QT_QML_IMPORT_PATH "")
foreach(_candidate "${_qt_prefix}/qml" "${_qt_prefix}/share/qt6/qml")
    if(EXISTS "${_candidate}/QtQuick")
        set(PET_QT_QML_IMPORT_PATH "${_candidate}")
    endif()
endforeach()

add_executable(test_settings_view test_settings_view.cpp)
target_link_libraries(test_settings_view PRIVATE pet_infrastructure pet_ui Qt6::Gui Qt6::Qml Qt6::Quick)
target_compile_definitions(test_settings_view PRIVATE
    PET_QML_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../qml"
    QT_QML_IMPORT_PATH="${PET_QT_QML_IMPORT_PATH}"
)
add_test(NAME TestSettingsView COMMAND test_settings_view)
set_tests_properties(TestSettingsView PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
