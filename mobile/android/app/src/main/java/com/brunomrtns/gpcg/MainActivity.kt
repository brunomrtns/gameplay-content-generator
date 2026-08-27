package com.brunomrtns.gpcg

import android.os.Bundle
import androidx.core.view.WindowCompat
import com.facebook.react.ReactActivity
import com.facebook.react.ReactActivityDelegate
import com.facebook.react.defaults.DefaultNewArchitectureEntryPoint.fabricEnabled
import com.facebook.react.defaults.DefaultReactActivityDelegate

class MainActivity : ReactActivity() {

  /**
   * Returns the name of the main component registered from JavaScript. This is used to schedule
   * rendering of the component.
   */
  override fun getMainComponentName(): String = "GpcgMobile"

  /**
   * Enable edge-to-edge display so the app draws behind system bars.
   * SafeAreaView in React Native handles the insets.
   *
   * Switch from SplashTheme to AppTheme here so the splash drawable
   * (shown by the system while the activity is creating) is replaced
   * by the normal app background once React Native starts rendering.
   */
  override fun onCreate(savedInstanceState: Bundle?) {
    // Switch from SplashTheme to AppTheme BEFORE super.onCreate
    setTheme(R.style.AppTheme)
    super.onCreate(null)
    // Edge-to-edge: let content draw behind system bars
    WindowCompat.setDecorFitsSystemWindows(window, false)
  }

  /**
   * Returns the instance of the [ReactActivityDelegate]. We use [DefaultReactActivityDelegate]
   * which allows you to enable New Architecture with a single boolean flags [fabricEnabled]
   */
  override fun createReactActivityDelegate(): ReactActivityDelegate =
      DefaultReactActivityDelegate(this, mainComponentName, fabricEnabled)
}
