import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

PanelWindow {
    id: panel
    property string phase: "Idle"
    property int seconds: 0
    property string phaseFile: Quickshell.env("JARVIS_PHASE_FILE")
    property string folder: phaseFile.slice(0, phaseFile.lastIndexOf("/") + 1)
    property bool serviceControls: Quickshell.env("JARVIS_SERVICE_CONTROLS") === "1"
    property bool singleTurn: Quickshell.env("JARVIS_SINGLE_TURN") === "1"
    property var request: ({})
    property bool awaitingApproval: !!request.token
    property bool submitted: false
    property string reply: ""
    property bool dismissed: false
    property bool expanded: awaitingApproval || reply.length > 0
    property bool followingUp: phase === "Listening for follow-up"

    onPhaseChanged: {
        if (phase === "Listening") {
            seconds = 0
            dismissed = false
        }
    }

    visible: awaitingApproval || phase !== "Idle" || (reply.length > 0 && !dismissed)
    implicitWidth: expanded ? 580 : 354
    implicitHeight: expanded ? replyCard.height + 31 : 91
    anchors.bottom: true
    margins.bottom: 52
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    WlrLayershell.namespace: "jarvis-status"

    FileView {
        path: panel.phaseFile
        watchChanges: true
        onFileChanged: reload()
        onLoaded: if (text().trim()) panel.phase = text().trim()
    }

    FileView {
        path: panel.folder + "approval"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: {
            try {
                const request = JSON.parse(text())
                if (request.token !== panel.request.token)
                    panel.submitted = false
                panel.request = request
            } catch (error) {
                panel.request = ({})
            }
        }
    }

    FileView {
        id: decision
        path: panel.folder + "decision"
    }

    FileView {
        path: panel.folder + "reply"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: panel.reply = text()
    }

    function decide(value) {
        if (!awaitingApproval || submitted)
            return
        submitted = true
        decision.setText(request.token + " " + value)
    }

    Process {
        id: control
        command: ["systemctl", "--user", "kill", "--kill-whom=main", "--signal=USR1", "jarvis.service"]
    }

    Process {
        id: cancelFollowUp
        command: ["kill", "-USR2", Quickshell.env("JARVIS_PID")]
    }

    Timer {
        interval: 1000
        running: panel.phase === "Listening"
        repeat: true
        onTriggered: panel.seconds++
    }

    Rectangle {
        x: 8
        y: 4
        width: 66
        height: 21
        radius: 10
        color: "#eb12171f"
        border.color: "#596578"

        Text {
            anchors.centerIn: parent
            text: "JARVIS"
            color: "#cfe2ff"
            font.pixelSize: 10
            font.letterSpacing: 1.4
            font.bold: true
        }
    }

    Rectangle {
        visible: !panel.expanded
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 60
        radius: 14
        color: "#ef171b22"
        border.color: "#62728a"

        Rectangle {
            anchors { left: parent.left; leftMargin: 15; verticalCenter: parent.verticalCenter }
            width: 8
            height: 8
            radius: 4
            color: panel.phase === "Listening" ? "#ff8f91" : "#9dc7ff"
        }

        Column {
            anchors { left: parent.left; leftMargin: 36; verticalCenter: parent.verticalCenter }
            spacing: 3

            Text {
                text: panel.phase === "Listening"
                    ? "Listening  ·  " + Math.floor(panel.seconds / 60) + ":" + ("0" + (panel.seconds % 60)).slice(-2)
                    : panel.phase
                color: "#f5f8ff"
                font.pixelSize: 14
                font.bold: true
            }

            Text {
                visible: panel.serviceControls
                text: panel.phase === "Listening" ? "Shift+F1 to send" : "Shift+F1 to silence"
                color: "#becadc"
                font.pixelSize: 11
            }
        }

        Rectangle {
            visible: panel.serviceControls
            anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
            width: 73
            height: 33
            radius: 8
            color: pointer.containsMouse ? "#445775" : "#303d51"

            Text {
                anchors.centerIn: parent
                text: panel.phase === "Listening" ? "Send" : "Silence"
                color: "#f5f8ff"
                font.pixelSize: 12
            }

            MouseArea {
                id: pointer
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: control.running = true
            }
        }
    }

    Rectangle {
        id: replyCard
        visible: panel.expanded
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: panel.awaitingApproval ? 325 : Math.min(
            Math.floor((panel.screen ? panel.screen.height : 720) * 0.65),
            Math.ceil(replyText.contentHeight * Math.min(1, 15 / Math.max(1, replyText.lineCount))
                      + replyText.topPadding + replyText.bottomPadding
                      + title.height + actions.implicitHeight + 56
                      + (subtitle.visible ? subtitle.height + 4 : 0))
        )
        radius: 14
        color: "#171b22"
        border.color: "#62728a"

        Text {
            id: title
            anchors { top: parent.top; left: parent.left; margins: 14 }
            text: panel.awaitingApproval ? panel.request.title : "Jarvis’s reply"
            textFormat: Text.PlainText
            color: "#f5f8ff"
            font.pixelSize: 17
            font.bold: true
        }

        Text {
            id: subtitle
            anchors { top: title.bottom; left: title.left; topMargin: 4 }
            visible: !panel.awaitingApproval && text.length > 0
            text: panel.followingUp ? "Listening…"
                  : panel.phase === "Thinking" || panel.phase === "Transcribing" ? "Thinking…" : ""
            color: "#becadc"
        }

        ScrollView {
            id: detailsScroll
            anchors {
                top: subtitle.visible ? subtitle.bottom : title.bottom
                bottom: actions.top
                left: parent.left; right: parent.right
                margins: 14
            }
            clip: true
            contentWidth: availableWidth
            contentHeight: replyText.implicitHeight
            ScrollBar.vertical.policy: contentHeight > availableHeight ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff

            TextArea {
                id: replyText
                width: detailsScroll.availableWidth
                height: implicitHeight
                text: panel.awaitingApproval ? panel.request.details : panel.reply
                textFormat: TextEdit.PlainText
                readOnly: true
                wrapMode: panel.awaitingApproval ? TextEdit.WrapAnywhere : TextEdit.Wrap
                color: "#e1e9f5"
                font.family: panel.awaitingApproval ? "monospace" : Qt.application.font.family
                font.pixelSize: panel.awaitingApproval ? 13 : 15
                onTextChanged: {
                    const flickable = detailsScroll.contentItem
                    if (flickable)
                        flickable.contentY = flickable.originY
                }
                background: Rectangle { radius: 7; color: "#10141b" }
            }
        }

        Row {
            id: actions
            anchors { right: parent.right; bottom: parent.bottom; margins: 14 }
            spacing: 10

            Button {
                visible: panel.awaitingApproval
                text: "Deny"
                enabled: !panel.submitted
                onClicked: panel.decide("decline")
            }

            Button {
                visible: panel.awaitingApproval
                text: panel.request.acceptLabel || "Approve once"
                enabled: !panel.submitted
                onClicked: panel.decide("accept")
            }

            Button {
                visible: !panel.awaitingApproval && panel.serviceControls
                         && panel.phase !== "Idle" && panel.phase !== "Listening for follow-up"
                text: "Silence"
                onClicked: control.running = true
            }

            Button {
                visible: !panel.awaitingApproval && (panel.phase === "Idle" || panel.followingUp)
                text: "Close"
                onClicked: {
                    panel.dismissed = true
                    if (panel.followingUp)
                        cancelFollowUp.running = true
                    else if (panel.singleTurn)
                        Qt.quit()
                }
            }
        }
    }
}
