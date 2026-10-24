import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;

/**
 * Standalone beep on STREAM/USAGE_ALARM — audible on secure lock screens
 * where SET_TIMER / cmd media play (MUSIC) stay silent until unlock.
 *
 * Run on device:
 *   CLASSPATH=/data/local/tmp/apk_updater_play_alarm.dex \
 *     app_process /data/local/tmp PlayAlarm [duration_ms]
 */
public final class PlayAlarm {
    private PlayAlarm() {}

    public static void main(String[] args) throws Exception {
        int durationMs = 8000;
        if (args != null && args.length > 0) {
            try {
                durationMs = Math.max(1000, Math.min(30000, Integer.parseInt(args[0])));
            } catch (NumberFormatException ignored) {
                // keep default
            }
        }

        final int sampleRate = 22050;
        final double freq = 880.0;
        final int beepOn = sampleRate * 2 / 5; // 400ms
        final int beepPeriod = sampleRate * 3 / 5; // 600ms cycle

        AudioAttributes attrs =
                new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .setFlags(AudioAttributes.FLAG_AUDIBILITY_ENFORCED)
                        .build();
        AudioFormat format =
                new AudioFormat.Builder()
                        .setSampleRate(sampleRate)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build();

        int minBuf =
                AudioTrack.getMinBufferSize(
                        sampleRate,
                        AudioFormat.CHANNEL_OUT_MONO,
                        AudioFormat.ENCODING_PCM_16BIT);
        if (minBuf <= 0) {
            minBuf = sampleRate / 2;
        }
        int bufSamples = Math.max(minBuf / 2, sampleRate / 5);

        AudioTrack track =
                new AudioTrack.Builder()
                        .setAudioAttributes(attrs)
                        .setAudioFormat(format)
                        .setBufferSizeInBytes(bufSamples * 2)
                        .setTransferMode(AudioTrack.MODE_STREAM)
                        .build();

        track.play();

        short[] chunk = new short[bufSamples];
        long framesWritten = 0;
        long framesTotal = (long) sampleRate * durationMs / 1000L;
        try {
            while (framesWritten < framesTotal) {
                int n = (int) Math.min(bufSamples, framesTotal - framesWritten);
                for (int i = 0; i < n; i++) {
                    long idx = framesWritten + i;
                    int pos = (int) (idx % beepPeriod);
                    if (pos < beepOn) {
                        double sample = Math.sin(2.0 * Math.PI * freq * idx / sampleRate);
                        // Soft envelope to reduce clicks
                        double env = 1.0;
                        if (pos < 200) {
                            env = pos / 200.0;
                        } else if (pos > beepOn - 200) {
                            env = (beepOn - pos) / 200.0;
                        }
                        chunk[i] = (short) (sample * env * 30000.0);
                    } else {
                        chunk[i] = 0;
                    }
                }
                int wrote = track.write(chunk, 0, n);
                if (wrote < 0) {
                    break;
                }
                framesWritten += wrote > 0 ? wrote : n;
            }
        } finally {
            try {
                track.stop();
            } catch (Exception ignored) {
                // ignore
            }
            track.release();
        }
    }
}
