package com.mediasearch.kiosk

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Handler
import android.os.Looper
import android.util.Log
import org.json.JSONObject
import org.vosk.LibVosk
import org.vosk.LogLevel
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.StorageService
import java.util.Locale

/**
 * On-device wake word detector using Vosk with a hand-rolled mic pipeline.
 *
 * Previously this class handed off to Vosk's built-in `SpeechService`, which
 * works but gives us no control over the mic path. Far-field wake detection
 * with a noisy TV in the same room was unreliable as a result: no echo
 * cancellation, no noise suppression, no automatic gain, and the wrong
 * audio source (DEFAULT instead of VOICE_RECOGNITION).
 *
 * This version owns its own `AudioRecord` and feeds raw PCM frames into
 * `Recognizer.acceptWaveForm` from a background thread. That buys us four
 * Alexa-style quality wins that were impossible before:
 *
 *   1. `AudioSource.VOICE_RECOGNITION` — tells the Android audio HAL this
 *      is a far-field voice pipeline so the vendor-tuned DSP kicks in on
 *      every modern tablet (Fire HD, Galaxy Tab, Pixel Tablet, etc.).
 *   2. `AcousticEchoCanceler` — cancels audio playing through the room's
 *      own speakers (the TV, the kiosk's own TTS, etc.) so it doesn't
 *      drown out a user calling "Hey Nova" from across the room.
 *   3. `NoiseSuppressor` — filters steady-state background noise (HVAC,
 *      dishwasher, white-noise machine in the nursery, TV dialog).
 *   4. `AutomaticGainControl` — boosts distant / quiet speech up to the
 *      recognizer's expected level so 15-ft wake phrases actually get
 *      through Vosk's acoustic model.
 *
 * Each effect is attached best-effort: if a device doesn't support it or
 * the HAL refuses, we log and continue. The raw mic pipeline still works.
 *
 * Grammar mode still constrains decoding to the wake phrases plus "[unk]",
 * but the variants list is widened (hay/hi/hey, ok/okay, plus bare token)
 * so common mis-recognitions on noisy audio aren't rejected outright.
 *
 * Lifecycle:
 *   - Call [prepare] once from onCreate after mic permission is granted.
 *     Unpacks the bundled `assets/model-en-us/` on first launch.
 *   - Call [start] to begin listening.
 *   - Call [stop] before the WebView takes the mic for tap-to-talk.
 *   - Call [setWakeWord] to reconfigure without restarting the service.
 *
 * Mic contention: Android's AudioRecord only allows one active consumer
 * per process, so the WebView MUST call [stop] before getUserMedia and
 * [start] afterwards. The [onWake] callback auto-stops before firing.
 */
class WakeWordManager(
    private val context: Context,
    private val onWake: () -> Unit,
    private val onReady: () -> Unit = {},
    private val onError: (String) -> Unit = {},
) {

    companion object {
        private const val TAG = "WakeWordMgr"
        private const val SAMPLE_RATE = 16000
        private const val COOLDOWN_MS = 1500L
        private const val MODEL_ASSET_DIR = "model-en-us"
        private const val MODEL_DEST_DIR = "vosk-model"

        // Read ~100 ms per loop — small enough for low wake latency,
        // large enough that we're not scheduler-bound.
        private const val READ_CHUNK_SAMPLES = 1600
    }

    private val mainHandler = Handler(Looper.getMainLooper())

    private var model: Model? = null
    private var currentWakeWord: String = "alfred"

    @Volatile
    private var running: Boolean = false
    private var lastTriggerAt: Long = 0L

    // Mic pipeline state. openMicPipeline / releaseMicPipeline both take
    // `pipelineLock` so a new start() can't race with the tail end of
    // a wake-word teardown.
    private val pipelineLock = Any()
    private var captureThread: Thread? = null
    private var audioRecord: AudioRecord? = null
    private var recognizer: Recognizer? = null
    private var aec: AcousticEchoCanceler? = null
    private var ns: NoiseSuppressor? = null
    private var agc: AutomaticGainControl? = null

    init {
        LibVosk.setLogLevel(LogLevel.WARNINGS)
    }

    val isPrepared: Boolean get() = model != null
    val isRunning: Boolean get() = running

    /** Unpack the model from assets to internal storage and load it. Idempotent. */
    fun prepare() {
        if (model != null) {
            onReady()
            return
        }
        Log.d(TAG, "Unpacking Vosk model from assets…")
        StorageService.unpack(
            context,
            MODEL_ASSET_DIR,
            MODEL_DEST_DIR,
            { loaded ->
                model = loaded
                Log.d(TAG, "Vosk model loaded")
                onReady()
            },
            { exception ->
                val msg = exception.message ?: "unknown"
                Log.e(TAG, "Failed to unpack Vosk model: $msg", exception)
                onError("Failed to load wake word model: $msg")
            },
        )
    }

    /** Change which wake word is listened for. Restarts the recognizer if running. */
    fun setWakeWord(word: String) {
        val normalised = word.trim().lowercase(Locale.US)
        if (normalised.isEmpty() || normalised == currentWakeWord) return
        Log.d(TAG, "Wake word changed: '$currentWakeWord' → '$normalised'")
        currentWakeWord = normalised
        if (running) {
            stop()
            start()
        }
    }

    fun getWakeWord(): String = currentWakeWord

    /** Begin listening. No-op if already running or not yet prepared. */
    fun start() {
        if (running) return
        val m = model ?: run {
            Log.w(TAG, "start() called before model finished loading — will retry on ready")
            return
        }
        try {
            openMicPipeline(m)
            running = true
            captureThread = Thread({ runCaptureLoop() }, "WakeWord-Capture").also {
                it.isDaemon = true
                it.start()
            }
            Log.d(TAG, "Listening for '$currentWakeWord'")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start wake word pipeline", e)
            running = false
            releaseMicPipeline()
            onError("Wake word start failed: ${e.message}")
        }
    }

    /** Stop listening and release the mic so another consumer can acquire it. */
    fun stop() {
        if (!running && audioRecord == null && captureThread == null) return
        running = false
        val thread = captureThread
        captureThread = null
        // Best-effort join so the capture loop finishes its current frame
        // and hits the release() path before we return control to the
        // caller. If we returned immediately the WebView's getUserMedia
        // could race the AudioRecord release.
        try {
            thread?.join(200)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        releaseMicPipeline()
        Log.d(TAG, "Stopped")
    }

    /** Release all resources. Safe to call multiple times. */
    fun destroy() {
        stop()
        model = null
    }

    // ───────────── mic pipeline ─────────────

    private fun openMicPipeline(model: Model) {
        synchronized(pipelineLock) {
            val minBuf = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            )
            if (minBuf <= 0) throw IllegalStateException("AudioRecord.getMinBufferSize returned $minBuf")
            // Over-size the ring buffer so scheduler hiccups don't drop frames.
            val bufferBytes = maxOf(minBuf * 4, READ_CHUNK_SAMPLES * 2 * 4)

            val record = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferBytes,
            )
            if (record.state != AudioRecord.STATE_INITIALIZED) {
                record.release()
                throw IllegalStateException("AudioRecord failed to initialise")
            }

            // Attach hardware voice-processing effects to this mic session.
            // Each is optional — older or oddball tablets may not support any
            // of them, and that's fine, we just lose that particular boost.
            val sessionId = record.audioSessionId
            aec = tryAttachEffect("AcousticEchoCanceler") {
                if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(sessionId)?.apply {
                    enabled = true
                } else null
            }
            ns = tryAttachEffect("NoiseSuppressor") {
                if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(sessionId)?.apply {
                    enabled = true
                } else null
            }
            agc = tryAttachEffect("AutomaticGainControl") {
                if (AutomaticGainControl.isAvailable()) AutomaticGainControl.create(sessionId)?.apply {
                    enabled = true
                } else null
            }

            recognizer = Recognizer(model, SAMPLE_RATE.toFloat(), buildGrammar(currentWakeWord))
            audioRecord = record
            record.startRecording()
        }
    }

    private inline fun <T> tryAttachEffect(name: String, block: () -> T?): T? =
        try {
            val effect = block()
            if (effect != null) Log.d(TAG, "$name enabled")
            else Log.d(TAG, "$name not available on this device")
            effect
        } catch (e: Exception) {
            Log.w(TAG, "$name attach failed: ${e.message}")
            null
        }

    private fun releaseMicPipeline() {
        synchronized(pipelineLock) {
            try { aec?.release() } catch (_: Exception) {}
            try { ns?.release() } catch (_: Exception) {}
            try { agc?.release() } catch (_: Exception) {}
            aec = null; ns = null; agc = null

            try {
                audioRecord?.let {
                    if (it.recordingState == AudioRecord.RECORDSTATE_RECORDING) it.stop()
                    it.release()
                }
            } catch (e: Exception) {
                Log.w(TAG, "AudioRecord release error: ${e.message}")
            }
            audioRecord = null

            try { recognizer?.close() } catch (_: Exception) {}
            recognizer = null
        }
    }

    private fun runCaptureLoop() {
        val record = audioRecord ?: return
        val rec = recognizer ?: return
        val buffer = ShortArray(READ_CHUNK_SAMPLES)
        Log.d(TAG, "Capture loop started")
        try {
            while (running) {
                val read = record.read(buffer, 0, buffer.size)
                if (read <= 0) {
                    if (read == AudioRecord.ERROR_INVALID_OPERATION ||
                        read == AudioRecord.ERROR_BAD_VALUE
                    ) {
                        Log.w(TAG, "AudioRecord.read error: $read — aborting capture")
                        break
                    }
                    continue
                }
                val finalized = try {
                    rec.acceptWaveForm(buffer, read)
                } catch (e: Exception) {
                    Log.w(TAG, "Recognizer acceptWaveForm threw: ${e.message}")
                    false
                }
                val hyp = try {
                    if (finalized) rec.result else rec.partialResult
                } catch (e: Exception) {
                    Log.w(TAG, "Recognizer result fetch threw: ${e.message}")
                    null
                }
                if (hyp != null && checkForWake(hyp, partial = !finalized)) {
                    // Wake fired — exit the loop so release runs on this
                    // thread before onWake's callback acquires the mic.
                    break
                }
            }
        } finally {
            Log.d(TAG, "Capture loop ended")
        }
    }

    // ───────────── wake detection ─────────────

    /**
     * Build a Vosk grammar string containing only phrases that should count
     * as a wake. Grammar mode dramatically reduces CPU vs. full-vocabulary
     * decoding and eliminates false positives outside the allow-list.
     *
     * The "[unk]" entry is required — it absorbs anything the user says
     * that isn't a wake phrase so the recognizer doesn't force-match.
     *
     * For every wake word we include several spoken-form variants. Vosk's
     * acoustic model matches strictly against the grammar tokens, so when
     * a far-field "Hey Nova" comes through a noisy channel and the decoder
     * thinks it heard "hi nova" or just "nova", we want those to count.
     */
    private fun buildGrammar(word: String): String {
        val w = word.trim().lowercase(Locale.US)
        val phrases = linkedSetOf(
            w,
            "hey $w",
            "hay $w",
            "hi $w",
            "a $w",
            "okay $w",
            "ok $w",
        )
        val json = buildString {
            append('[')
            phrases.forEachIndexed { i, p ->
                if (i > 0) append(',')
                append('"').append(p).append('"')
            }
            append(",\"[unk]\"]")
        }
        Log.d(TAG, "Grammar: $json")
        return json
    }

    /**
     * Inspect a hypothesis JSON blob and fire the wake callback if it
     * contains the target phrase. Returns `true` if a wake fired so the
     * capture loop can exit cleanly.
     */
    private fun checkForWake(hypothesisJson: String, partial: Boolean): Boolean {
        val text = try {
            val obj = JSONObject(hypothesisJson)
            if (partial) obj.optString("partial", "") else obj.optString("text", "")
        } catch (_: Exception) {
            ""
        }
        if (text.isEmpty() || text == "[unk]") return false

        val lower = text.lowercase(Locale.US)
        if (!lower.contains(currentWakeWord)) return false

        val now = System.currentTimeMillis()
        if (now - lastTriggerAt < COOLDOWN_MS) return false
        lastTriggerAt = now

        Log.d(TAG, "Wake hit: '$text' (partial=$partial)")

        // We're on the capture thread. Release the mic synchronously here
        // (releaseMicPipeline is synchronized on pipelineLock) so that by
        // the time onWake() is dispatched on the main thread the mic is
        // already free and the WebView's getUserMedia can acquire it.
        // Doing the release on a Handler.post would let a racing start()
        // open a new pipeline that the posted task then destroys.
        running = false
        releaseMicPipeline()
        mainHandler.post { onWake() }
        return true
    }
}
