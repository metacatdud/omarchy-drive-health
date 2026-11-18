import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.plugin as Plugin

ShellRoot {
  Plugin.ReportSource { id: source; demo: true }
  FloatingWindow {
    id: window
    title: "Drive Health · prototype demo"
    visible: true
    implicitWidth: Style.space(460)
    implicitHeight: Style.space(800)
    color: Commons.Color.background
    Shortcut { sequence: "R"; onActivated: source.refresh() }
    Shortcut { sequence: "Escape"; onActivated: Qt.quit() }
    ScrollView {
      anchors.fill: parent
      anchors.margins: Style.space(24)
      contentWidth: availableWidth
      clip: true
      id: scroll
      Plugin.DriveList { width: scroll.availableWidth; report: source.report }
    }
  }
}
