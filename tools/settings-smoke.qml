import QtQuick
import Quickshell
import qs.Commons as Commons
import qs.plugin as Plugin

ShellRoot {
  FloatingWindow {
    visible: true
    title: "Drive Health · settings test"
    implicitWidth: 400
    implicitHeight: 480
    color: Commons.Color.background
    Plugin.WarrantyEditor {
      id: editor
      anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
      configPath: Quickshell.env("DRIVE_HEALTH_TEST_CONFIG")
      property int stage: 0
      Component.onCompleted: {
        if (!configPath) throw new Error("A temporary test config is required")
        load({id: "aaaaaaaaaaaaaaaaaaaaaaaa", model: "Test drive — temporary settings", warranty: {}})
        warrantyUntil = "2030-02-30"
        ratedTbw = "600"
        save()
      }
      onErrorCodeChanged: {
        if (errorCode === "invalid_date" && stage === 0) {
          stage = 1
          retry.start()
        }
      }
      onSaved: {
        if (stage !== 1) throw new Error("Invalid settings were accepted")
        console.log("SETTINGS_SAVE_OK")
        Qt.quit()
      }
    }
    Timer {
      id: retry
      interval: 300
      onTriggered: {
        editor.warrantyUntil = "2030-02-28"
        editor.ratedTbw = "600.5"
        editor.save()
      }
    }
  }
  Timer { interval: 6000; running: true; onTriggered: { console.error("SETTINGS_TEST_TIMEOUT"); Qt.quit() } }
}
