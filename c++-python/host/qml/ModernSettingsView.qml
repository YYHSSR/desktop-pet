import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Window {
    id: settingsWindow
    objectName: "settingsWindow"
    title: "桌宠设置"
    width: 800
    height: 620
    minimumWidth: 720
    minimumHeight: 520
    color: "#f8f9fa"
    visible: false
    property int currentPage: 0

    // 主开关联动的子控件使能状态（对齐 Python _update_self_talk_controls /
    // _update_translucency_controls）
    property bool selfTalkOn: Boolean(configManager.value("self_talk_enabled", false))
    property bool translucentOn: Boolean(configManager.value("context_menu_appearance.translucent", true))

    Connections {
        target: (typeof configManager !== 'undefined') ? configManager : null
        function onValueChanged(key, val) {
            if (key === "self_talk_enabled") settingsWindow.selfTalkOn = Boolean(val)
            else if (key === "context_menu_appearance.translucent") settingsWindow.translucentOn = Boolean(val)
        }
    }

    ListModel {
        id: pageModel
        ListElement { title: "常规"; iconText: "⚙"; keywords: "开机 自启 通知 窗口 置顶 全屏" }
        ListElement { title: "桌宠行为"; iconText: "▷"; keywords: "动画 播放 速度 间隔 移动 拖拽 弹射 漫步 点击 自言自语 候选 图片 台词 绑定 Agent 思考" }
        ListElement { title: "外观"; iconText: "◐"; keywords: "大小 透明度 气泡 方案 菜单 主题 密度 圆角 字体 字号 颜色 浅色 深色 彩蛋 头像" }
        ListElement { title: "启动应用"; iconText: "▣"; keywords: "应用 程序 路径 添加 删除 图标" }
        ListElement { title: "快捷网址"; iconText: "◎"; keywords: "网页 网站 链接 URL" }
    }
    ListModel { id: appModel }
    ListModel { id: websiteModel }

    // Python catalog.SCALE_STEPS = (0.5, 0.72, 0.85, 1.0)，标签为 round(640*s) px；
    // 当前值不在档位内时追加一项（Python scale_combo / speed_select 行为）。
    function scaleValues() {
        var steps = [0.5, 0.72, 0.85, 1.0]
        var current = Number(configManager.value("scale", 0.72))
        var found = false
        for (var i = 0; i < steps.length; ++i) if (Math.abs(steps[i] - current) < 0.001) found = true
        if (!found) {
            steps.push(current)
            steps.sort(function (a, b) { return a - b })
        }
        return steps
    }
    function scaleLabels() {
        var values = scaleValues()
        var out = []
        for (var i = 0; i < values.length; ++i) out.push(Math.round(640 * values[i]) + " px")
        return out
    }

    function speedValues() {
        var steps = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
        var current = Number(configManager.value("playback_speed", 1.0))
        var found = false
        for (var i = 0; i < steps.length; ++i) if (Math.abs(steps[i] - current) < 0.001) found = true
        if (!found) {
            steps.push(current)
            steps.sort(function (a, b) { return a - b })
        }
        return steps
    }
    function speedLabels() {
        var values = speedValues()
        var out = []
        for (var i = 0; i < values.length; ++i) out.push(String(values[i]) + "x")
        return out
    }

    function loadEditors() {
        appModel.clear()
        var apps = configManager.quickLaunchApps
        for (var i = 0; i < apps.length; ++i)
            appModel.append({ "name": apps[i].name || "应用", "path": apps[i].path || "", "kind": apps[i].kind || "application", "checked": false })
        websiteModel.clear()
        var sites = configManager.quickWebsites
        for (var j = 0; j < sites.length; ++j)
            websiteModel.append({ "name": sites[j].name || "", "url": sites[j].url || "", "checked": false })
    }

    function saveApps() {
        var result = []
        for (var i = 0; i < appModel.count; ++i) {
            var item = appModel.get(i)
            result.push({ "name": item.name, "path": item.path, "kind": item.kind })
        }
        configManager.quickLaunchApps = result
    }

    function saveWebsites() {
        var result = []
        for (var i = 0; i < websiteModel.count; ++i) {
            var item = websiteModel.get(i)
            result.push({ "name": item.name, "url": item.url })
        }
        configManager.quickWebsites = result
    }

    // 列表项图标：app/... 取真实程序图标（默认浏览器与取不到时回退矢量图标），
    // vector/... 取内置矢量图标；与右键菜单共用同一套 MenuIcons 绘制。
    function appIconUrl(kind, path) {
        return "image://menuicon/app/" + (kind || "application") + "/" + encodeURIComponent(path || "")
    }
    function vectorIconUrl(name) {
        return "image://menuicon/vector/" + name
    }

    // 对齐 Python QuickLaunchEditor：添加应用 / 添加默认浏览器 / 移除勾选
    function appendApp(data) {
        appModel.append({ "name": data.name || "应用", "path": data.path || "",
                          "kind": data.kind || "application", "checked": false })
        saveApps()
    }
    function addDefaultBrowser() {
        for (var i = 0; i < appModel.count; ++i)
            if (appModel.get(i).kind === "default_browser") return
        appendApp({ "name": "默认浏览器", "path": "", "kind": "default_browser" })
    }
    function removeCheckedApps() {
        for (var i = appModel.count - 1; i >= 0; --i)
            if (appModel.get(i).checked) appModel.remove(i)
        appList.currentIndex = -1
        saveApps()
    }

    // 对齐 Python WebsiteLinksEditor：移除勾选 / 恢复默认网址
    function removeCheckedWebsites() {
        for (var i = websiteModel.count - 1; i >= 0; --i)
            if (websiteModel.get(i).checked) websiteModel.remove(i)
        websiteList.currentIndex = -1
        saveWebsites()
    }
    function resetWebsites() {
        websiteModel.clear()
        websiteModel.append({ "name": "GitHub 项目页", "url": "https://github.com/MerZlin/dsh-pet-indesktop", "checked": false })
        saveWebsites()
    }

    function searchSettings(query) {
        var needle = query.trim().toLowerCase()
        if (!needle.length) { searchStatus.text = ""; return }
        for (var i = 0; i < pageModel.count; ++i) {
            var page = pageModel.get(i)
            if ((page.title + " " + page.keywords).toLowerCase().indexOf(needle) >= 0) {
                currentPage = i
                sidebar.currentIndex = i
                searchStatus.text = "1 个匹配 · " + page.title
                return
            }
        }
        searchStatus.text = "未找到匹配的设置"
    }

    onVisibleChanged: if (visible) loadEditors()

    onClosing: (close) => {
        saveApps()
        saveWebsites()
        if (typeof configManager !== 'undefined' && configManager) {
            configManager.save()
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.preferredWidth: 188
            Layout.fillHeight: true
            color: "#f1f3f5"
            border.color: "#dfe3e8"
            border.width: 1
            ColumnLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                anchors.topMargin: 16
                anchors.bottomMargin: 12
                spacing: 9
                Button {
                    Layout.fillWidth: true
                    implicitHeight: 34
                    text: "‹  保存并退出"
                    font.pixelSize: 13
                    font.family: "Microsoft YaHei UI"
                    onClicked: { saveApps(); saveWebsites(); configManager.save(); settingsWindow.close() }
                }
                TextField {
                    Layout.fillWidth: true
                    implicitHeight: 34
                    placeholderText: "⌕  搜索设置…"
                    font.pixelSize: 12
                    font.family: "Microsoft YaHei UI"
                    onTextChanged: searchSettings(text)
                }
                Label {
                    id: searchStatus
                    Layout.fillWidth: true
                    visible: text.length > 0
                    color: "#6b7177"
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                }
                ListView {
                    id: sidebar
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: pageModel
                    currentIndex: settingsWindow.currentPage
                    spacing: 2
                    clip: true
                    delegate: Rectangle {
                        required property int index
                        required property string title
                        required property string iconText
                        width: ListView.view.width
                        height: 34
                        radius: 8
                        color: sidebar.currentIndex === index ? "#dfe3e8" : (hovered.hovered ? "#e7eaed" : "transparent")
                        Row {
                            anchors.left: parent.left
                            anchors.leftMargin: 12
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 9
                            Label { text: iconText; color: "#4d555d"; font.pixelSize: 15; width: 17; horizontalAlignment: Text.AlignHCenter }
                            Label { text: title; color: "#30343a"; font.pixelSize: 13; font.family: "Microsoft YaHei UI" }
                        }
                        HoverHandler { id: hovered }
                        TapHandler { onTapped: { settingsWindow.currentPage = index; sidebar.currentIndex = index } }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#fafbfc"
            ColumnLayout {
                anchors.fill: parent
                anchors.leftMargin: 28
                anchors.rightMargin: 26
                anchors.topMargin: 22
                anchors.bottomMargin: 18
                spacing: 12
                Label {
                    text: pageModel.get(settingsWindow.currentPage).title
                    color: "#202124"
                    font.pixelSize: 23
                    font.bold: true
                    font.family: "Microsoft YaHei UI"
                }
                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: settingsWindow.currentPage

                    // ==================================================== 常规
                    ScrollView {
                        id: generalScroll
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: generalScroll.availableWidth
                            spacing: 16
                            SettingsCard {
                                title: "应用启动"
                                SettingsToggleRow { settingKey: "__autostart__"; title: "开机自启"; description: "登录系统后自动启动桌宠。" }
                                SettingsToggleRow { settingKey: "system_notifications_enabled"; title: "系统通知"; description: "允许重要事件显示桌面通知。"; defaultValue: true }
                            }
                            SettingsCard {
                                title: "窗口与系统"
                                SettingsToggleRow { settingKey: "on_top"; title: "窗口置顶"; description: "始终将桌宠保持在其他窗口上方。"; defaultValue: true }
                                SettingsToggleRow { settingKey: "auto_hide_fullscreen"; title: "全屏时自动隐藏"; description: "全屏游戏或视频期间自动隐藏桌宠。"; defaultValue: true }
                            }
                        }
                    }

                    // ================================================= 桌宠行为
                    ScrollView {
                        id: behaviorScroll
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: behaviorScroll.availableWidth
                            spacing: 16
                            SettingsCard {
                                title: "动画"
                                SettingsChoiceRow { settingKey: "playback_speed"; title: "播放速率"; description: "控制所有桌宠动画的播放速度。"; labels: settingsWindow.speedLabels(); values: settingsWindow.speedValues(); defaultValue: 1.0 }
                                SettingsSliderRow { settingKey: "animation_gap_seconds"; title: "动作等待间隔"; description: "非待机动作之间的休息时间；0 秒表示连续播放。"; from: 0; to: 3600; stepSize: 0.5; decimals: 1; defaultValue: 0; suffix: " 秒" }
                                SettingsToggleRow { settingKey: "no_move"; title: "不移动"; description: "暂停桌宠在桌面上的自动移动。" }
                                SettingsToggleRow { settingKey: "music_sing_enabled"; title: "音乐自动唱歌"; description: "检测到后台播放音乐时，自动播放唱歌动画。" }
                            }
                            SettingsCard {
                                title: "拖拽"
                                SettingsToggleRow { settingKey: "lock_position"; title: "锁定位置"; description: "桌宠固定不动，无法拖动（点击互动仍有效）。" }
                                SettingsToggleRow { settingKey: "shift_drag"; title: "SHIFT+左键拖动"; description: "开启后必须按住 SHIFT 再左键才能拖动桌宠。" }
                            }
                            SettingsCard {
                                title: "漫步与调头"
                                SettingsToggleRow { settingKey: "smart_edge_turn"; title: "边缘智能调头"; description: "漫步贴近屏幕边缘且前方空间不足时，自动转身面向开阔区域，避免在角落卡住。"; defaultValue: true }
                                SettingsToggleRow { settingKey: "smooth_wander"; title: "平滑加减速漫步"; description: "漫步移动时启用 SmoothStep 平滑加减速缓动，起步与停步更轻柔自然。"; defaultValue: true }
                            }
                            SettingsCard {
                                title: "点击反馈"
                                SettingsToggleRow { settingKey: "click_show_self_talk"; title: "点击触发自言自语"; description: "点击时随机显示一条自言自语内容。" }
                            }
                            SettingsCard {
                                title: "自言自语"
                                SettingsToggleRow { settingKey: "self_talk_enabled"; title: "气泡自言自语"; description: "让桌宠偶尔显示一条随机思考气泡。" }
                                SettingsSliderRow { enabled: settingsWindow.selfTalkOn; settingKey: "self_talk_duration_seconds"; title: "显示时间"; description: "每条文字或图片气泡保持显示的时间。"; from: 1; to: 300; stepSize: 0.5; decimals: 1; defaultValue: 3.2; suffix: " 秒" }
                                SettingsSliderRow { enabled: settingsWindow.selfTalkOn; settingKey: "self_talk_min_interval"; title: "最短间隔"; description: "上一条气泡消失后，到下一条出现前的最短空闲时间。"; from: 5; to: 3600; stepSize: 1; defaultValue: 20; suffix: " 秒" }
                                SettingsSliderRow { enabled: settingsWindow.selfTalkOn; settingKey: "self_talk_max_interval"; title: "最长间隔"; description: "上一条气泡消失后，到下一条出现前的最长空闲时间。"; from: 5; to: 3600; stepSize: 1; defaultValue: 60; suffix: " 秒" }
                                SettingsTextAreaRow { enabled: settingsWindow.selfTalkOn; settingKey: "self_talk_texts"; title: "候选内容"; description: "每行一条；留空时恢复内置文本。"; valueIsList: true; defaultValue: configManager.defaultSelfTalkTexts() }
                                SettingsPathRow { enabled: settingsWindow.selfTalkOn; settingKey: "self_talk_image_dir"; title: "图片目录"; description: "从目录中的常见图片格式随机选择；默认使用内置彩蛋图片池，留空时只显示文本。"; directory: true }
                                SettingsButtonRow { enabled: settingsWindow.selfTalkOn; title: "点击动画台词绑定"; description: "为每个点击动画设置专属自言自语台词。"; buttonText: "编辑…"; buttonWidth: 84; onActivated: petController.openClickTalkBindings() }
                            }
                            SettingsCard {
                                title: "Agent 联动 · 思考气泡文案"
                                SettingsTextRow { settingKey: "agent_thinking_antigravity"; mode: "agentThinking"; agentKey: "antigravity"; title: "Antigravity IDE 思考文案"; description: "默认：Antigravity 正在深度思考……；支持 {name} 占位符；留空用默认。"; placeholderText: "Antigravity 正在深度思考……"; fieldWidth: 280 }
                                SettingsTextRow { settingKey: "agent_thinking_chatgpt"; mode: "agentThinking"; agentKey: "chatgpt"; title: "ChatGPT 思考文案"; description: "默认：ChatGPT 正在认真思考，我陪你等～；支持 {name} 占位符；留空用默认。"; placeholderText: "ChatGPT 正在认真思考，我陪你等～"; fieldWidth: 280 }
                            }
                        }
                    }

                    // ==================================================== 外观
                    ScrollView {
                        id: appearanceScroll
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: appearanceScroll.availableWidth
                            spacing: 16
                            SettingsCard {
                                title: "桌宠显示"
                                SettingsChoiceRow { settingKey: "scale"; title: "桌宠大小"; description: "调整桌宠在桌面上的显示尺寸。"; labels: settingsWindow.scaleLabels(); values: settingsWindow.scaleValues(); defaultValue: 0.72 }
                                SettingsSliderRow { settingKey: "pet_opacity"; title: "不透明度"; description: "调整桌宠窗口的整体透明度；100% 为完全不透明。"; from: 10; to: 100; stepSize: 1; defaultValue: 100; suffix: "%" }
                                SettingsChoiceRow { settingKey: "self_talk_bubble_style"; title: "气泡方案"; description: "选择气泡视觉与相对桌宠的位置；贴近屏幕边缘时自动换位。"; labels: ["经典暖黄 · 正上方", "纸感卡片 · 左上方", "深色玻璃 · 右上方", "柔蓝对话 · 正上方", "吐气水泡 · 左上方"]; values: ["classic_top", "paper_left", "glass_right", "soft_blue_top", "breath_bubble"]; defaultValue: "classic_top" }
                            }
                            SettingsCard {
                                title: "菜单外观"
                                SettingsChoiceRow { settingKey: "context_menu_appearance.theme"; title: "颜色主题"; description: "可跟随系统，或固定使用浅色/深色菜单。"; labels: ["跟随系统", "浅色", "深色"]; values: ["system", "light", "dark"]; defaultValue: "system" }
                                SettingsChoiceRow { settingKey: "context_menu_appearance.density"; title: "菜单密度"; description: "调整新版右键菜单的菜单项高度和分组留白。"; labels: ["紧凑", "标准", "宽松"]; values: ["compact", "standard", "spacious"]; defaultValue: "standard" }
                                SettingsChoiceRow { settingKey: "context_menu_appearance.corner_radius"; title: "圆角大小"; description: "调整新版右键菜单和子菜单的外轮廓圆角。"; labels: ["8 px", "12 px", "16 px", "18 px"]; values: [8, 12, 16, 18]; defaultValue: 12 }
                                SettingsFontRow { settingKey: "context_menu_appearance.ui_font"; title: "UI 字体"; description: "设置新版菜单使用的界面字体。"; defaultValue: "system" }
                                SettingsChoiceRow { settingKey: "context_menu_appearance.ui_font_size"; title: "UI 字号"; description: "同步调整主菜单与多级菜单的字号。"; labels: ["10 px", "11 px", "12 px", "13 px", "14 px", "15 px", "16 px", "17 px", "18 px"]; values: [10, 11, 12, 13, 14, 15, 16, 17, 18]; defaultValue: 13 }
                                SettingsToggleRow { settingKey: "context_menu_appearance.translucent"; title: "半透明菜单"; description: "使用接近 Modern 的半透明浮层表面。"; defaultValue: true }
                                SettingsSliderRow { enabled: settingsWindow.translucentOn; settingKey: "context_menu_appearance.opacity"; title: "表面不透明度"; description: "调整菜单背景透出桌面内容的程度。"; from: 0.72; to: 1.0; stepSize: 0.02; decimals: 2; defaultValue: 0.94 }
                            }
                            SettingsCard {
                                title: "浅色主题"
                                SettingsColorRow { settingKey: "context_menu_appearance.light_background"; title: "背景色"; description: "浅色菜单的浮层背景。"; defaultValue: "#ffffff" }
                                SettingsColorRow { settingKey: "context_menu_appearance.light_foreground"; title: "文字色"; description: "浅色菜单的主要文字与图标颜色。"; defaultValue: "#171717" }
                                SettingsColorRow { settingKey: "context_menu_appearance.light_hover"; title: "悬停色"; description: "鼠标悬停菜单项时的背景。"; defaultValue: "#eeeeee" }
                            }
                            SettingsCard {
                                title: "深色主题"
                                SettingsColorRow { settingKey: "context_menu_appearance.dark_background"; title: "背景色"; description: "深色菜单的浮层背景。"; defaultValue: "#252525" }
                                SettingsColorRow { settingKey: "context_menu_appearance.dark_foreground"; title: "文字色"; description: "深色菜单的主要文字与图标颜色。"; defaultValue: "#f3f3f3" }
                                SettingsColorRow { settingKey: "context_menu_appearance.dark_hover"; title: "悬停色"; description: "鼠标悬停菜单项时的背景。"; defaultValue: "#3a3a3a" }
                            }
                            SettingsCard {
                                title: "彩蛋入口"
                                SettingsToggleRow { settingKey: "menu_easter_egg.enabled"; title: "显示彩蛋"; description: "控制新版菜单首行彩蛋入口是否显示。"; defaultValue: true }
                                SettingsTextRow { settingKey: "menu_easter_egg.title"; title: "入口标题"; description: "显示在圆形头像右侧的文字。"; defaultValue: "厉害了我的鲸"; maxLength: 40 }
                                SettingsTextRow { settingKey: "menu_easter_egg.hint"; title: "右侧提示"; description: "显示在鼠标指针图标后的短提示。"; defaultValue: "请点击"; maxLength: 20; fieldWidth: 160 }
                                SettingsPathRow { settingKey: "menu_easter_egg.avatar"; title: "头像图片"; description: "使用绝对路径；支持常见图片格式。"; assetMode: "eggAvatar" }
                                SettingsPathRow { settingKey: "menu_easter_egg.image_dir"; title: "弹窗图片目录"; description: "使用绝对路径；每次点击会随机选择一张图片。"; assetMode: "eggImageDir"; directory: true }
                            }
                        }
                    }

                    // ================================================ 启动应用
                    ScrollView {
                        id: appScroll
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: appScroll.availableWidth
                            SettingsCard {
                                Label { Layout.fillWidth: true; text: "启动应用"; color: "#202124"; font.pixelSize: 13; font.bold: true }
                                Label { Layout.fillWidth: true; text: "这些应用将按图标和名称显示在右键菜单的“启动应用”子菜单中。"; color: "#6b7177"; font.pixelSize: 11; wrapMode: Text.WordWrap }
                                ListView {
                                    id: appList
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: Math.max(116, Math.min(240, contentHeight))
                                    model: appModel
                                    clip: true
                                    spacing: 3
                                    // Python QuickLaunchEditor：InternalMove 拖拽排序（列表顺序 = 菜单项顺序）
                                    property int dragIndex: -1
                                    delegate: Rectangle {
                                        id: appRow
                                        required property int index
                                        required property string name
                                        required property string path
                                        required property string kind
                                        required property bool checked
                                        width: ListView.view.width
                                        height: 34
                                        radius: 7
                                        color: appList.dragIndex === index ? "#d5e8ff"
                                             : appList.currentIndex === index ? "#e8f2ff" : "#f6f7f8"
                                        MouseArea {
                                            id: appDragHandle
                                            width: 24; height: parent.height
                                            cursorShape: Qt.SizeAllCursor
                                            onPressed: appList.dragIndex = appRow.index
                                            onPositionChanged: {
                                                if (appList.dragIndex < 0 || !(mouse.buttons & Qt.LeftButton)) return
                                                var pos = mapToItem(appList, mouse.x, mouse.y)
                                                var target = Math.max(0, Math.min(appModel.count - 1, Math.floor(pos.y / 37)))
                                                if (target !== appList.dragIndex) {
                                                    appModel.move(appList.dragIndex, target, 1)
                                                    appList.dragIndex = target
                                                }
                                            }
                                            onReleased: { if (appList.dragIndex >= 0) { appList.dragIndex = -1; settingsWindow.saveApps() } }
                                            onCanceled: appList.dragIndex = -1
                                        }
                                        Label { visible: appDragHandle.pressed; text: "⠿"; anchors.left: parent.left; anchors.leftMargin: 4; anchors.verticalCenter: parent.verticalCenter; color: "#7a8699"; font.pixelSize: 12 }
                                        RowLayout { anchors.left: parent.left; anchors.leftMargin: 6; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                                            // 勾选必须写回 ListModel：required property 的赋值不会同步模型，
                                            // 而移除勾选读的是模型数据——不同步会导致“删除不了”。
                                            CheckBox { checked: appRow.checked; onToggled: appModel.setProperty(appRow.index, "checked", checked) }
                                            Image { source: settingsWindow.appIconUrl(kind, path); sourceSize.width: 18; sourceSize.height: 18; Layout.preferredWidth: 18; Layout.preferredHeight: 18; fillMode: Image.PreserveAspectFit }
                                            Label { Layout.fillWidth: true; text: name; color: "#202124"; font.pixelSize: 12; elide: Text.ElideRight }
                                        }
                                        ToolTip.visible: appRowHover.hovered
                                        ToolTip.text: kind === "default_browser" ? "使用系统默认浏览器" : path
                                        HoverHandler { id: appRowHover }
                                        TapHandler { onTapped: appList.currentIndex = index; onDoubleTapped: { appEditDialog.editIndex = index; appName.text = name; appPath.text = path; appKind.currentIndex = kind === "default_browser" ? 0 : 1; appEditDialog.open() } }
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Button { text: "添加应用"; icon.source: settingsWindow.vectorIconUrl("add"); onClicked: { appFileDialog.addMode = true; appFileDialog.open() } }
                                    Button { text: "编辑应用"; icon.source: settingsWindow.vectorIconUrl("settings"); enabled: appList.currentIndex >= 0; onClicked: { var item = appModel.get(appList.currentIndex); appEditDialog.editIndex = appList.currentIndex; appName.text = item.name; appPath.text = item.path; appKind.currentIndex = item.kind === "default_browser" ? 0 : 1; appEditDialog.open() } }
                                    Button { text: "添加默认浏览器"; icon.source: settingsWindow.vectorIconUrl("web"); onClicked: settingsWindow.addDefaultBrowser() }
                                    Item { Layout.fillWidth: true }
                                    Button { text: "移除勾选"; icon.source: settingsWindow.vectorIconUrl("remove"); onClicked: settingsWindow.removeCheckedApps() }
                                }
                            }
                        }
                    }

                    // ================================================ 快捷网址
                    ScrollView {
                        id: webScroll
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: webScroll.availableWidth
                            SettingsCard {
                                Label { Layout.fillWidth: true; text: "快捷网址"; color: "#202124"; font.pixelSize: 13; font.bold: true }
                                Label { Layout.fillWidth: true; text: "配置右键菜单“快捷网址”子菜单中的网页。可自定义命名；未命名时直接显示网址。"; color: "#6b7177"; font.pixelSize: 11; wrapMode: Text.WordWrap }
                                ListView {
                                    id: websiteList
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: Math.max(116, Math.min(240, contentHeight))
                                    model: websiteModel
                                    clip: true
                                    spacing: 3
                                    // Python WebsiteEditDialog 列表：同样的 InternalMove 拖拽排序
                                    property int dragIndex: -1
                                    delegate: Rectangle {
                                        id: webRow
                                        required property int index
                                        required property string name
                                        required property string url
                                        required property bool checked
                                        width: ListView.view.width
                                        height: 34
                                        radius: 7
                                        color: websiteList.dragIndex === index ? "#d5e8ff"
                                             : websiteList.currentIndex === index ? "#e8f2ff" : "#f6f7f8"
                                        MouseArea {
                                            id: webDragHandle
                                            width: 24; height: parent.height
                                            cursorShape: Qt.SizeAllCursor
                                            onPressed: websiteList.dragIndex = webRow.index
                                            onPositionChanged: {
                                                if (websiteList.dragIndex < 0 || !(mouse.buttons & Qt.LeftButton)) return
                                                var pos = mapToItem(websiteList, mouse.x, mouse.y)
                                                var target = Math.max(0, Math.min(websiteModel.count - 1, Math.floor(pos.y / 37)))
                                                if (target !== websiteList.dragIndex) {
                                                    websiteModel.move(websiteList.dragIndex, target, 1)
                                                    websiteList.dragIndex = target
                                                }
                                            }
                                            onReleased: { if (websiteList.dragIndex >= 0) { websiteList.dragIndex = -1; settingsWindow.saveWebsites() } }
                                            onCanceled: websiteList.dragIndex = -1
                                        }
                                        Label { visible: webDragHandle.pressed; text: "⠿"; anchors.left: parent.left; anchors.leftMargin: 4; anchors.verticalCenter: parent.verticalCenter; color: "#7a8699"; font.pixelSize: 12 }
                                        RowLayout { anchors.left: parent.left; anchors.leftMargin: 6; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                                            // 同上：勾选状态必须写回模型，否则「移除勾选」永远读到 false
                                            CheckBox { checked: webRow.checked; onToggled: websiteModel.setProperty(webRow.index, "checked", checked) }
                                            Image { source: settingsWindow.vectorIconUrl("web"); sourceSize.width: 18; sourceSize.height: 18; Layout.preferredWidth: 18; Layout.preferredHeight: 18; fillMode: Image.PreserveAspectFit }
                                            Label { Layout.fillWidth: true; text: name.length ? (name + " (" + url + ")") : url; color: "#202124"; font.pixelSize: 12; elide: Text.ElideRight }
                                        }
                                        ToolTip.visible: webRowHover.hovered
                                        ToolTip.text: "网址: " + url + "\n名称: " + (name.length ? name : "（默认显示网址）")
                                        HoverHandler { id: webRowHover }
                                        TapHandler { onTapped: websiteList.currentIndex = index; onDoubleTapped: { websiteEditDialog.editIndex = index; websiteName.text = name; websiteUrl.text = url; websiteEditDialog.open() } }
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Button { text: "添加网址"; icon.source: settingsWindow.vectorIconUrl("add"); onClicked: { websiteEditDialog.editIndex = -1; websiteName.text = ""; websiteUrl.text = ""; websiteEditDialog.open() } }
                                    Button { text: "编辑网址"; icon.source: settingsWindow.vectorIconUrl("settings"); enabled: websiteList.currentIndex >= 0; onClicked: { var item = websiteModel.get(websiteList.currentIndex); websiteEditDialog.editIndex = websiteList.currentIndex; websiteName.text = item.name; websiteUrl.text = item.url; websiteEditDialog.open() } }
                                    Button { text: "恢复默认网址"; icon.source: settingsWindow.vectorIconUrl("refresh"); onClicked: settingsWindow.resetWebsites() }
                                    Item { Layout.fillWidth: true }
                                    Button { text: "移除勾选"; icon.source: settingsWindow.vectorIconUrl("remove"); onClicked: settingsWindow.removeCheckedWebsites() }
                                }
                            }
                        }
                    }

                }
            }
        }
    }

    Dialog {
        id: appEditDialog
        property int editIndex: -1
        title: editIndex < 0 ? "添加快捷应用" : "编辑快捷应用"
        modal: true
        anchors.centerIn: Overlay.overlay
        standardButtons: Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            width: 430
            spacing: 10
            Label { text: "类型" }
            ComboBox { id: appKind; Layout.fillWidth: true; model: ["默认浏览器", "应用程序"] }
            Label { text: "显示名称" }
            TextField { id: appName; Layout.fillWidth: true; placeholderText: "例如：记事本" }
            Label { text: "应用路径"; visible: appKind.currentIndex === 1 }
            RowLayout { Layout.fillWidth: true; visible: appKind.currentIndex === 1
                TextField { id: appPath; Layout.fillWidth: true; placeholderText: "C:\\路径\\应用.exe" }
                Button { text: "浏览…"; onClicked: { appFileDialog.addMode = false; appFileDialog.open() } }
            }
        }
        onAccepted: {
            var kind = appKind.currentIndex === 0 ? "default_browser" : "application"
            var cleanName = appName.text.trim()
            if (!cleanName.length) cleanName = kind === "default_browser" ? "默认浏览器" : appPath.text.split(/[\\/]/).pop().replace(/\.[^.]+$/, "")
            var data = { "name": cleanName, "path": kind === "default_browser" ? "" : appPath.text.trim(), "kind": kind }
            if (editIndex < 0) {
                settingsWindow.appendApp(data)
            } else {
                data["checked"] = appModel.get(editIndex).checked
                appModel.set(editIndex, data)
                saveApps()
            }
        }
    }
    FileDialog {
        id: appFileDialog
        // addMode：点「添加应用」直接新增条目（对齐 Python _choose_application）；
        // 否则仅把选中的路径回填到编辑对话框。
        property bool addMode: true
        title: "选择需要启动的应用"
        nameFilters: ["应用程序 (*.exe *.lnk *.bat *.cmd)", "所有文件 (*)"]
        onAccepted: {
            var picked = configManager.localPath(selectedFile)
            if (addMode) {
                settingsWindow.appendApp({ "name": picked.split(/[\\/]/).pop().replace(/\.[^.]+$/, ""), "path": picked, "kind": "application" })
            } else {
                appPath.text = picked
            }
        }
    }
    Dialog {
        id: websiteEditDialog
        property int editIndex: -1
        title: editIndex < 0 ? "添加快捷网址" : "编辑快捷网址"
        modal: true
        anchors.centerIn: Overlay.overlay
        standardButtons: Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            width: 430
            spacing: 10
            Label { text: "显示名称（可留空）" }
            TextField { id: websiteName; Layout.fillWidth: true; placeholderText: "例如：项目主页" }
            Label { text: "网址" }
            TextField { id: websiteUrl; Layout.fillWidth: true; placeholderText: "https://example.com" }
        }
        onAccepted: {
            var url = websiteUrl.text.trim()
            if (!url.length) return
            if (url.indexOf("http://") !== 0 && url.indexOf("https://") !== 0 && url.indexOf("file://") !== 0) url = "https://" + url
            var data = { "name": websiteName.text.trim(), "url": url }
            if (editIndex < 0) {
                data["checked"] = false
                websiteModel.append(data)
            } else {
                data["checked"] = websiteModel.get(editIndex).checked
                websiteModel.set(editIndex, data)
            }
            saveWebsites()
        }
    }
}
