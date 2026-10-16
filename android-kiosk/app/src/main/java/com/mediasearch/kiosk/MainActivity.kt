package com.mediasearch.kiosk

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.MotionEvent
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.webkit.ConsoleMessage
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "LumaKiosk"
        private const val BASE_URL = "https://tflixapp.com/"
        private const val VERSION_URL = "https://tflixapp.com/api/version"
        private const val POLL_INTERVAL_MS = 30_000L
        private const val RELOAD_COOLDOWN_MS = 5_000L
        private const val TAP_ZONE_PX = 80
        private const val TAP_COUNT_REQUIRED = 5
        private const val TAP_WINDOW_MS = 3_000L
        private const val MIC_PERMISSION_CODE = 1001
    }

    private lateinit var webView: WebView
    private lateinit var reconnectOverlay: TextView
    private val handler = Handler(Looper.getMainLooper())

    private var lastSeenVersion: String? = null
    private var lastReloadTime = 0L
    private var pollingActive = false

    // Corner-tap hard reset
    private var tapCount = 0
    private var firstTapTime = 0L

    // Pending WebView permission request (for mic)
    private var pendingPermissionRequest: PermissionRequest? = null

    // On-device wake word (Vosk). Runs continuously while the sheet is
    // closed; releases the mic so WebView getUserMedia can acquire it for
    // tap-to-talk recording.
    private var wakeWord: WakeWordManager? = null
    private var wakeWordShouldRun: Boolean = true

    private val versionCheckRunnable = object : Runnable {
        override fun run() {
            if (pollingActive) {
                checkVersion()
                handler.postDelayed(this, POLL_INTERVAL_MS)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        reconnectOverlay = findViewById(R.id.reconnectOverlay)

        enterImmersiveMode()
        requestMicPermissionIfNeeded()
        configureWebView()
        initWakeWord()
        webView.loadUrl(BASE_URL)
    }

    override fun onResume() {
        super.onResume()
        enterImmersiveMode()
        startVersionPolling()
        // Re-arm the wake word if it was paused by backgrounding the activity.
        if (wakeWordShouldRun && hasMicPermission()) {
            wakeWord?.start()
        }
    }

    override fun onPause() {
        super.onPause()
        stopVersionPolling()
        // Release the mic whenever we're not front-and-center so other apps
        // (and screen-off states) don't contend with our AudioRecord loop.
        wakeWord?.stop()
    }

    override fun onDestroy() {
        super.onDestroy()
        stopVersionPolling()
        wakeWord?.destroy()
        webView.destroy()
    }

    private fun hasMicPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    // ── Immersive / fullscreen ──

    private fun enterImmersiveMode() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.insetsController?.let { ctrl ->
                ctrl.hide(WindowInsets.Type.systemBars())
                ctrl.systemBarsBehavior =
                    WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = (
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    or View.SYSTEM_UI_FLAG_FULLSCREEN
                    or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                    or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                )
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) enterImmersiveMode()
    }

    // ── Wake word (on-device, Google-free) ──

    private fun initWakeWord() {
        wakeWord = WakeWordManager(
            context = applicationContext,
            onWake = { dispatchWakeToWebView() },
            onReady = {
                if (wakeWordShouldRun && hasMicPermission()) {
                    wakeWord?.start()
                }
            },
            onError = { msg -> Log.w(TAG, "WakeWord: $msg") },
        )
        // Unpacking the model is async (runs off the main thread inside
        // StorageService), so it's safe to kick it off before mic permission
        // has been granted. The onReady callback gates the actual start() on
        // the permission state.
        wakeWord?.prepare()
    }

    private fun dispatchWakeToWebView() {
        // The WakeWordManager has already called stop() synchronously before
        // invoking onWake, so the mic is free for getUserMedia by the time
        // this runnable executes on the UI thread.
        handler.post {
            webView.evaluateJavascript(
                "window.dispatchEvent(new CustomEvent('luma-native-wake'));",
                null,
            )
        }
    }

    // ── Microphone permission ──

    private fun requestMicPermissionIfNeeded() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.RECORD_AUDIO),
                MIC_PERMISSION_CODE
            )
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == MIC_PERMISSION_CODE) {
            val granted = grantResults.isNotEmpty() &&
                grantResults[0] == PackageManager.PERMISSION_GRANTED
            // If we had a pending WebView permission request, grant/deny it now
            pendingPermissionRequest?.let { req ->
                if (granted) {
                    req.grant(req.resources)
                } else {
                    req.deny()
                }
                pendingPermissionRequest = null
            }
            Log.d(TAG, "Microphone permission ${if (granted) "granted" else "denied"}")
            if (granted && wakeWordShouldRun) {
                wakeWord?.start()
            }
        }
    }

    // ── WebView ──

    private fun configureWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            cacheMode = WebSettings.LOAD_DEFAULT
            databaseEnabled = true
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            useWideViewPort = true
            loadWithOverviewMode = true
            allowFileAccess = false
            allowContentAccess = false
        }

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                reconnectOverlay.visibility = View.GONE
            }

            override fun onReceivedError(
                view: WebView?, request: WebResourceRequest?, error: WebResourceError?
            ) {
                super.onReceivedError(view, request, error)
                if (request?.isForMainFrame == true) {
                    reconnectOverlay.visibility = View.VISIBLE
                    handler.postDelayed({ webView.loadUrl(BASE_URL) }, 5000)
                }
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            // Grant microphone access when the web page requests it
            // (SpeechRecognition API, getUserMedia for voice recording)
            override fun onPermissionRequest(request: PermissionRequest?) {
                request ?: return
                val resources = request.resources
                val needsAudio = resources.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)

                if (needsAudio) {
                    // Check if Android permission is already granted
                    if (ContextCompat.checkSelfPermission(
                            this@MainActivity, Manifest.permission.RECORD_AUDIO
                        ) == PackageManager.PERMISSION_GRANTED
                    ) {
                        request.grant(resources)
                    } else {
                        // Stash the request and ask the user for permission
                        pendingPermissionRequest = request
                        requestMicPermissionIfNeeded()
                    }
                } else {
                    request.grant(resources)
                }
            }

            override fun onConsoleMessage(consoleMessage: ConsoleMessage?): Boolean {
                consoleMessage?.let {
                    Log.d(TAG, "[WebView] ${it.message()} (${it.sourceId()}:${it.lineNumber()})")
                }
                return true
            }
        }

        // Expose a small bridge to the web app so it can coordinate with the
        // native wake word detector. All methods run on a binder thread — do
        // NOT touch WebView state directly from inside them; bounce back to
        // the main thread.
        webView.addJavascriptInterface(LumaBridge(), "LumaKiosk")
    }

    private inner class LumaBridge {
        @JavascriptInterface
        fun isNativeWakeWordAvailable(): Boolean = wakeWord != null

        @JavascriptInterface
        fun pauseWakeWord() {
            wakeWordShouldRun = false
            handler.post { wakeWord?.stop() }
        }

        @JavascriptInterface
        fun resumeWakeWord() {
            wakeWordShouldRun = true
            handler.post {
                if (hasMicPermission()) wakeWord?.start()
            }
        }

        @JavascriptInterface
        fun setWakeWord(word: String?) {
            if (word.isNullOrBlank()) return
            handler.post { wakeWord?.setWakeWord(word) }
        }

        @JavascriptInterface
        fun getWakeWord(): String = wakeWord?.getWakeWord() ?: ""
    }

    // ── Navigation ──

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        }
        // Don't call super — prevent leaving the kiosk
    }

    // ── Corner-tap hard reset ──

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        if (event.action == MotionEvent.ACTION_DOWN) {
            val x = event.x.toInt()
            val y = event.y.toInt()
            // Top-left corner tap detection
            if (x < TAP_ZONE_PX && y < TAP_ZONE_PX) {
                val now = System.currentTimeMillis()
                if (now - firstTapTime > TAP_WINDOW_MS) {
                    tapCount = 0
                    firstTapTime = now
                }
                tapCount++
                if (tapCount >= TAP_COUNT_REQUIRED) {
                    tapCount = 0
                    hardReset()
                }
            } else {
                tapCount = 0
            }
        }
        return super.dispatchTouchEvent(event)
    }

    private fun hardReset() {
        Log.d(TAG, "Hard reset — clearing WebView and reloading")
        webView.clearCache(true)
        webView.clearHistory()
        webView.loadUrl(BASE_URL)
    }

    // ── Version polling for auto-reload ──

    private fun startVersionPolling() {
        if (!pollingActive) {
            pollingActive = true
            handler.postDelayed(versionCheckRunnable, POLL_INTERVAL_MS)
        }
    }

    private fun stopVersionPolling() {
        pollingActive = false
        handler.removeCallbacks(versionCheckRunnable)
    }

    private fun checkVersion() {
        Thread {
            try {
                val conn = URL(VERSION_URL).openConnection() as HttpURLConnection
                conn.connectTimeout = 5000
                conn.readTimeout = 5000
                val version = conn.inputStream.bufferedReader().readText().trim()
                conn.disconnect()

                handler.post {
                    if (lastSeenVersion != null && version != lastSeenVersion) {
                        val now = System.currentTimeMillis()
                        if (now - lastReloadTime > RELOAD_COOLDOWN_MS) {
                            Log.d(TAG, "Version changed: $lastSeenVersion -> $version, reloading")
                            lastReloadTime = now
                            webView.reload()
                        }
                    }
                    lastSeenVersion = version
                    reconnectOverlay.visibility = View.GONE
                }
            } catch (e: Exception) {
                handler.post {
                    reconnectOverlay.visibility = View.VISIBLE
                }
            }
        }.start()
    }
}
