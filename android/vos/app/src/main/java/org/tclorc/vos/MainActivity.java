package org.tclorc.vos;

import android.app.Activity;
import android.os.Bundle;
import android.os.SystemClock;
import android.graphics.Color;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Offline, non-root Android vOS container. No INTERNET permission or external content. */
public final class MainActivity extends Activity {
    private static final String HOST = "appassets.androidplatform.net";
    private static final String ORIGIN = "https://" + HOST + "/assets/index.html";
    private static final Set<String> ASSETS = new HashSet<>(Arrays.asList(
            "index.html", "style.css", "app.js"));
    private static final String CSP = "default-src 'none'; script-src 'self'; " +
            "style-src 'self'; img-src 'self' data:; connect-src 'none'; " +
            "worker-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'";
    private WebView browser;
    private final Object profileLock = new Object();
    private GameKernel kernel;
    private String activeProfile = "player";
    private final EntropyMeter entropy = new EntropyMeter();

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(6, 13, 26));
        getWindow().setNavigationBarColor(Color.rgb(6, 13, 26));
        try {
            kernel = new GameKernel(profileFile(activeProfile));
        } catch (IOException ex) {
            showFatal("Could not verify your private game save: " + ex.getMessage());
            return;
        }
        browser = new WebView(this);
        WebSettings settings = browser.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        browser.setWebViewClient(new PrivateAssets());
        browser.addJavascriptInterface(new GameBridge(), "TCLNative");
        browser.setBackgroundColor(Color.rgb(6, 13, 26));
        setContentView(browser);
        browser.loadUrl(ORIGIN);
    }

    private File profileFile(String profile) {
        return new File(getFilesDir(), "wallet-" + profile + ".journal");
    }

    private void showFatal(String message) {
        android.widget.TextView label = new android.widget.TextView(this);
        label.setBackgroundColor(Color.BLACK);
        label.setTextColor(Color.WHITE);
        label.setPadding(35, 45, 35, 25);
        label.setText("TCL vOS safety stop\n\n" + message +
                "\n\nYour file was NOT reset. Use device backup or seek support.");
        setContentView(label);
    }

    private static boolean local(Uri uri) {
        return "https".equals(uri.getScheme()) && HOST.equals(uri.getHost()) &&
                uri.getPort() == -1 && uri.getPath() != null &&
                uri.getPath().startsWith("/assets/") &&
                ASSETS.contains(uri.getPath().substring("/assets/".length()));
    }

    private final class PrivateAssets extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            return !local(request.getUrl());
        }
        @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            if (!local(uri)) return blocked();
            String asset = uri.getPath().substring("/assets/".length());
            String mime = asset.endsWith(".html") ? "text/html" :
                    asset.endsWith(".css") ? "text/css" : "text/javascript";
            try {
                InputStream in = getAssets().open(asset);
                Map<String, String> headers = new HashMap<>();
                headers.put("Content-Security-Policy", CSP);
                headers.put("X-Content-Type-Options", "nosniff");
                headers.put("Cache-Control", "no-store");
                headers.put("Referrer-Policy", "no-referrer");
                return new WebResourceResponse(mime, "utf-8", 200, "OK", headers, in);
            } catch (IOException ex) { return blocked(); }
        }
        private WebResourceResponse blocked() {
            return new WebResourceResponse("text/plain", "utf-8", 404,
                    "Not Found", new HashMap<>(),
                    new java.io.ByteArrayInputStream(new byte[0]));
        }
    }

    private JSONObject stateJson(GameKernel.Snapshot s) throws Exception {
        JSONObject o = new JSONObject();
        o.put("profile", activeProfile);
        o.put("wallet", new JSONObject().put("credits", s.credits).put("issued", s.issued)
                .put("sunk", s.sunk).put("unit", "τ").put("localOnly", true));
        o.put("civitas", new JSONObject().put("turn", s.turn)
                .put("shiftAvailable", s.lastWorkTurn != s.turn)
                .put("inventory", new JSONArray(s.inventory))
                .put("buy", new JSONArray(GameKernel.MARKET_BUY))
                .put("sell", new JSONArray(GameKernel.MARKET_SELL)));
        o.put("nightdrive", new JSONObject().put("x", s.x).put("y", s.y)
                .put("heading", s.heading).put("goalX", s.goalX).put("goalY", s.goalY)
                .put("fuel", s.fuel).put("deliveries", s.deliveries)
                .put("turbo", s.turbo).put("paint", s.paint));
        o.put("events", s.eventCount).put("digest", s.hash);
        o.put("message", s.message).put("delta", s.delta);
        o.put("history", new JSONArray(s.recent));
        o.put("interaction", new JSONObject().put("samples", entropy.sampleCount())
                .put("normalizedEntropy", entropy.normalized()).put("label", entropy.label())
                .put("advisoryOnly", true));
        return o;
    }

    private String fail(Exception e) {
        try {
            return new JSONObject().put("ok", false).put("error", e.getMessage() == null ?
                    "Request rejected" : e.getMessage()).toString();
        } catch (org.json.JSONException impossible) {
            return "{\"ok\":false,\"error\":\"Request rejected\"}";
        }
    }

    /** Exposes only host-validated verbs; JavaScript cannot mint or edit balances. */
    public final class GameBridge {
        @JavascriptInterface public String state() {
            synchronized (profileLock) {
                try { return new JSONObject().put("ok", true)
                        .put("state", stateJson(kernel.snapshot())).toString(); }
                catch (Exception e) { return fail(e); }
            }
        }

        @JavascriptInterface public String selectProfile(String profile) {
            synchronized (profileLock) {
                try {
                    if (profile == null || !profile.matches("[A-Za-z0-9_-]{2,24}"))
                        throw new IllegalArgumentException("Choose 2–24 letters, numbers, _ or -");
                    String normalized = profile.toLowerCase(Locale.ROOT);
                    GameKernel candidate = new GameKernel(profileFile(normalized));
                    kernel = candidate; activeProfile = normalized;
                    return new JSONObject().put("ok", true)
                            .put("state", stateJson(kernel.snapshot())).toString();
                } catch (Exception e) { return fail(e); }
            }
        }

        @JavascriptInterface public String command(String game, String action, int arg, String id) {
            synchronized (profileLock) {
                try {
                    return new JSONObject().put("ok", true)
                            .put("state", stateJson(kernel.dispatch(game, action, arg, id)))
                            .toString();
                } catch (Exception e) { return fail(e); }
            }
        }

        @JavascriptInterface public void observeInteraction() {
            entropy.record(SystemClock.elapsedRealtime());
        }
    }

    @Override protected void onDestroy() {
        if (browser != null) {browser.destroy(); browser = null;}
        super.onDestroy();
    }
}
