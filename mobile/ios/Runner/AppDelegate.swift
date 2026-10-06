import Flutter
import UIKit
import GoogleMaps

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private var mapsConfigured = false

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    if let rawKey = Bundle.main.object(forInfoDictionaryKey: "GoogleMapsAPIKey") as? String {
      let key = rawKey.trimmingCharacters(in: .whitespacesAndNewlines)
      if !key.isEmpty, !key.hasPrefix("$(") {
        mapsConfigured = GMSServices.provideAPIKey(key)
      }
    }
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let configured = mapsConfigured
    let channel = FlutterMethodChannel(
      name: "tw.heavyparking/config",
      binaryMessenger: engineBridge.applicationRegistrar.messenger()
    )
    channel.setMethodCallHandler { call, result in
      if call.method == "mapsConfigured" {
        result(configured)
      } else {
        result(FlutterMethodNotImplemented)
      }
    }
  }
}
