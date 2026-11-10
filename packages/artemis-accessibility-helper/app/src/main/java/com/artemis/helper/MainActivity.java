package com.artemis.helper;

import android.accessibilityservice.AccessibilityService;
import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Home screen of the helper: the only thing a person can open and tap on the phone itself.
 *
 * Until 1.3.0 the APK had no activity at all (no icon, nothing to open), so on a device
 * without a computer there was no way to enable the accessibility service or to see
 * whether it works. This screen shows the three things that must be true, in order, with a
 * button for each step, and a test that proves the service can read the screen and act.
 *
 * It never sets the session token: that stays reserved to the adb shell (see TokenReceiver).
 */
public class MainActivity extends Activity {
    private final Handler ui = new Handler(Looper.getMainLooper());
    private TextView stepService, stepServer, stepToken, testResult;
    private Button btnTest;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(root);
        setContentView(scroll);
        final int pad = dp(16);
        root.setPadding(pad, pad, pad, pad);
        // targetSdk 35 draws edge to edge: keep the content out of the system bars.
        scroll.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
            @SuppressWarnings("deprecation")
            @Override
            public WindowInsets onApplyWindowInsets(View v, WindowInsets insets) {
                v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                        insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
                return insets;
            }
        });

        root.addView(text("Artemis Helper", 26, true));
        root.addView(text("Version " + versionName() + " · aide d'accessibilité pour Artemis", 14, false));

        root.addView(heading("1. Service d'accessibilité"));
        stepService = text("…", 16, false);
        root.addView(stepService);
        root.addView(button("Ouvrir Réglages > Accessibilité", new View.OnClickListener() {
            @Override public void onClick(View v) { openAccessibilitySettings(); }
        }));
        TextView hint = text("Si l'interrupteur est grisé ou refusé (Android 13 et plus, app installée "
                + "hors Play Store) : appuie ci-dessous, puis ⋮ en haut à droite > « Autoriser les "
                + "paramètres restreints », confirme, et reviens activer le service. Si le ⋮ n'apparaît pas, "
                + "essaie d'abord d'activer le service une fois pour que le message s'affiche.", 14, false);
        root.addView(hint);
        root.addView(button("Ouvrir la fiche de l'app", new View.OnClickListener() {
            @Override public void onClick(View v) { openAppDetails(); }
        }));

        root.addView(heading("2. Serveur local"));
        stepServer = text("…", 16, false);
        root.addView(stepServer);

        root.addView(heading("3. Jeton de session"));
        stepToken = text("…", 16, false);
        root.addView(stepToken);
        root.addView(text("Le jeton est envoyé par l'ordinateur qui pilote le téléphone (adb). "
                + "Il n'est jamais saisi ici.", 13, false));

        root.addView(heading("Test"));
        btnTest = button("Tester la lecture d'écran et un geste", new View.OnClickListener() {
            @Override public void onClick(View v) { runTest(); }
        });
        root.addView(btnTest);
        testResult = text("Le test lit cet écran, puis envoie un « Retour à l'accueil ». "
                + "Tu dois voir l'écran d'accueil apparaître.", 14, false);
        root.addView(testResult);

        root.addView(button("Actualiser l'état", new View.OnClickListener() {
            @Override public void onClick(View v) { refresh(); }
        }));
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    // ------------------------------------------------------------------ state

    private boolean serviceEnabledInSettings() {
        String flat = Settings.Secure.getString(getContentResolver(),
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        if (TextUtils.isEmpty(flat)) return false;
        ComponentName me = new ComponentName(this, ArtemisAccessibilityService.class);
        TextUtils.SimpleStringSplitter split = new TextUtils.SimpleStringSplitter(':');
        split.setString(flat);
        while (split.hasNext()) {
            ComponentName c = ComponentName.unflattenFromString(split.next());
            if (c != null && c.equals(me)) return true;
        }
        return false;
    }

    private void refresh() {
        boolean enabled = serviceEnabledInSettings();
        boolean connected = ArtemisAccessibilityService.getInstance() != null;
        if (connected) {
            setStep(stepService, true, "Actif et connecté.");
        } else if (enabled) {
            setStep(stepService, false, "Activé dans les réglages, mais pas encore connecté. "
                    + "Attends quelques secondes puis actualise. Si ça persiste : désactive puis réactive le service.");
        } else {
            setStep(stepService, false, "Désactivé. Appuie sur « Ouvrir Réglages > Accessibilité », "
                    + "choisis « Artemis Accessibility Helper » et active-le.");
        }
        setStep(stepServer, false, "Vérification…");
        setStep(stepToken, false, "Vérification…");
        new Thread(new Runnable() {
            @Override public void run() { probeServer(); }
        }).start();
    }

    private void probeServer() {
        String body = null;
        String error = null;
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(
                    "http://127.0.0.1:" + ArtemisAccessibilityService.DEFAULT_PORT + "/ping").openConnection();
            c.setConnectTimeout(1500);
            c.setReadTimeout(1500);
            InputStream in = c.getInputStream();
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[1024];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            body = new String(out.toByteArray(), StandardCharsets.UTF_8);
        } catch (Throwable t) {
            error = t.getClass().getSimpleName() + (t.getMessage() == null ? "" : ": " + t.getMessage());
        }
        final String b = body;
        final String e = error;
        ui.post(new Runnable() {
            @Override public void run() {
                if (b != null) {
                    setStep(stepServer, true, "Répond sur 127.0.0.1:" + ArtemisAccessibilityService.DEFAULT_PORT + ".");
                    boolean tokenSet = b.replace(" ", "").contains("\"token_set\":true");
                    if (tokenSet) {
                        setStep(stepToken, true, "Reçu : l'ordinateur est connecté.");
                    } else {
                        setStep(stepToken, false, "Pas encore reçu. Normal si aucun ordinateur n'a encore "
                                + "piloté ce téléphone : lance Artemis côté ordinateur (artemis helper install).");
                    }
                } else {
                    setStep(stepServer, false, "Ne répond pas (" + e + "). Le service doit d'abord être actif.");
                    setStep(stepToken, false, "Impossible à vérifier sans le serveur.");
                }
            }
        });
    }

    // ------------------------------------------------------------------ test

    private void runTest() {
        ArtemisAccessibilityService svc = ArtemisAccessibilityService.getInstance();
        if (svc == null) {
            testResult.setText("✗ Le service n'est pas connecté : fais l'étape 1 d'abord.");
            return;
        }
        int nodes = -1;
        String pkg = "?";
        try {
            AccessibilityNodeInfo root = svc.getRootInActiveWindow();
            if (root != null) {
                pkg = String.valueOf(root.getPackageName());
                nodes = count(root, 0);
                root.recycle();
            }
        } catch (Throwable t) {
            testResult.setText("✗ Lecture d'écran impossible : " + t.getMessage());
            return;
        }
        final String readLine = nodes >= 0
                ? "✓ Lecture d'écran : " + nodes + " éléments vus dans « " + pkg + " »."
                : "✗ Lecture d'écran : aucune fenêtre active lisible.";
        testResult.setText(readLine + "\nGeste dans 2 s : retour à l'accueil…");
        btnTest.setEnabled(false);
        ui.postDelayed(new Runnable() {
            @Override public void run() {
                ArtemisAccessibilityService s = ArtemisAccessibilityService.getInstance();
                boolean ok = s != null && s.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME);
                testResult.setText(readLine + "\n" + (ok
                        ? "✓ Geste envoyé : tu as dû voir l'accueil. Reviens dans l'app pour la suite."
                        : "✗ Le geste n'a pas été accepté."));
                btnTest.setEnabled(true);
            }
        }, 2000);
    }

    private int count(AccessibilityNodeInfo node, int depth) {
        if (node == null || depth > 40) return 0;
        int total = 1;
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                total += count(child, depth + 1);
                child.recycle();
            }
        }
        return total;
    }

    // ------------------------------------------------------------------ navigation

    private void openAccessibilitySettings() {
        try {
            startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
        } catch (Throwable t) {
            startActivity(new Intent(Settings.ACTION_SETTINGS));
        }
    }

    private void openAppDetails() {
        Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
        i.setData(Uri.parse("package:" + getPackageName()));
        startActivity(i);
    }

    // ------------------------------------------------------------------ ui helpers

    private String versionName() {
        try {
            String v = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
            return v == null ? "?" : v;
        } catch (Throwable t) {
            return "?";
        }
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                getResources().getDisplayMetrics());
    }

    private TextView text(String s, int sp, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        if (bold) t.setTypeface(t.getTypeface(), android.graphics.Typeface.BOLD);
        t.setPadding(0, dp(4), 0, dp(4));
        return t;
    }

    private TextView heading(String s) {
        TextView t = text(s, 18, true);
        t.setPadding(0, dp(18), 0, dp(2));
        return t;
    }

    private Button button(String s, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(s);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(6);
        lp.gravity = Gravity.CENTER_HORIZONTAL;
        b.setLayoutParams(lp);
        b.setMinHeight(dp(52));
        return b;
    }

    private void setStep(TextView t, boolean ok, String msg) {
        t.setText((ok ? "✓ " : "✗ ") + msg);
        t.setTextColor(ok ? 0xFF0B6B3A : 0xFF8C1D18);
    }
}
