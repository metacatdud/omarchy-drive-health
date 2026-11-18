import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons as Commons
import qs.plugin as Plugin

ShellRoot {
  FileView {
    id: warningFixtures
    path: Qt.resolvedUrl("plugin/tests/fixtures/warnings.json").toString().replace(/^file:\/\//, "")
    blockLoading: true
  }
  QtObject {
    id: testBar
    property color foreground: Commons.Color.foreground
    property color barForeground: foreground
    property color urgent: Commons.Color.urgent
    property string fontFamily: Commons.Style.font.family
    property bool vertical: false
    property bool foregroundAnimationEnabled: false
    property int barSize: 30
    property string position: "top"
    property var activePopout: null
    property var clickTargets: []
    function hideTooltip(item) {}
    function showTooltip() {}
    function requestPopout(key) { activePopout = key }
    function releasePopout(key) { activePopout = null }
  }
  PanelWindow {
    Item {
      visible: false
  Repeater {
    model: JSON.parse(warningFixtures.text()).drives
    Plugin.DriveList {
      required property var modelData
      visible: false
      width: 350
      report: ({demo: true, stale: false, drives: [modelData], errors: []})
    }
  }

    }

    anchors { top: true; right: true }
    implicitWidth: 40
    implicitHeight: 30
    color: Commons.Color.background
    Plugin.BarWidget {
      id: widget
      anchors.fill: parent
      bar: testBar
      settings: ({demo: Quickshell.env("DRIVE_HEALTH_SMOKE_LIVE") !== "1"})
    }
  }
  Timer { interval: 1000; running: true; onTriggered: widget.open() }
  Timer { interval: 2500; running: true; onTriggered: widget.close() }
  Timer { interval: 3000; running: true; onTriggered: Qt.quit() }
}
