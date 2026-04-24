/**
 * frida_ssl_bypass.js
 * Universal SSL/TLS pinning bypass for Android apps.
 *
 * Usage (with frida-tools installed):
 *   frida -U -f com.vlv.aravali.reels -l mitm_addons/frida_ssl_bypass.js --no-pause
 *
 * Or attach to running process:
 *   frida -U -n "KukuTV" -l mitm_addons/frida_ssl_bypass.js
 */

Java.perform(function () {
    console.log("[*] SSL Pinning bypass script loaded — KukuTV / com.vlv.aravali.reels");

    // ── 1. TrustManagerImpl (Conscrypt) ─────────────────────────────────────
    try {
        var TrustManagerImpl = Java.use("com.android.org.conscrypt.TrustManagerImpl");
        TrustManagerImpl.verifyChain.implementation = function (
            untrustedChain, trustAnchorChain, host, clientAuth, ocspData, tlsSctData
        ) {
            console.log("[+] TrustManagerImpl.verifyChain → bypassed for: " + host);
            return untrustedChain;
        };
    } catch (e) { console.log("[-] TrustManagerImpl: " + e); }

    // ── 2. OkHttp3 CertificatePinner ────────────────────────────────────────
    try {
        var CertificatePinner = Java.use("okhttp3.CertificatePinner");
        CertificatePinner.check.overload("java.lang.String", "java.util.List").implementation = function (host, certs) {
            console.log("[+] OkHttp3 CertificatePinner.check → bypassed for: " + host);
        };
    } catch (e) { console.log("[-] OkHttp3 CertificatePinner (List): " + e); }

    try {
        var CertificatePinner2 = Java.use("okhttp3.CertificatePinner");
        CertificatePinner2.check.overload("java.lang.String", "[Ljava.security.cert.Certificate;").implementation = function (host, certs) {
            console.log("[+] OkHttp3 CertificatePinner.check (arr) → bypassed for: " + host);
        };
    } catch (e) {}

    // ── 3. X509TrustManager — accept all ────────────────────────────────────
    try {
        var X509TrustManager = Java.use("javax.net.ssl.X509TrustManager");
        var SSLContext = Java.use("javax.net.ssl.SSLContext");

        var TrustAll = Java.registerClass({
            name: "com.bypass.TrustAllManager",
            implements: [X509TrustManager],
            methods: {
                checkClientTrusted: function (chain, authType) {},
                checkServerTrusted: function (chain, authType) {
                    console.log("[+] TrustAllManager.checkServerTrusted → bypassed");
                },
                getAcceptedIssuers: function () { return []; },
            },
        });

        SSLContext.init.overload(
            "[Ljavax.net.ssl.KeyManager;",
            "[Ljavax.net.ssl.TrustManager;",
            "java.security.SecureRandom"
        ).implementation = function (km, tm, sr) {
            console.log("[+] SSLContext.init → injecting TrustAllManager");
            this.init(km, [TrustAll.$new()], sr);
        };
    } catch (e) { console.log("[-] SSLContext/TrustManager: " + e); }

    // ── 4. WebViewClient SSL error ───────────────────────────────────────────
    try {
        var WebViewClient = Java.use("android.webkit.WebViewClient");
        WebViewClient.onReceivedSslError.implementation = function (view, handler, error) {
            console.log("[+] WebViewClient.onReceivedSslError → proceeding");
            handler.proceed();
        };
    } catch (e) { console.log("[-] WebViewClient: " + e); }

    // ── 5. Network Security Config (Android 7+) ──────────────────────────────
    try {
        var NetworkSecurityPolicy = Java.use("android.security.NetworkSecurityPolicy");
        NetworkSecurityPolicy.isCleartextTrafficPermitted.overload("java.lang.String").implementation = function (host) {
            return true;
        };
        NetworkSecurityPolicy.isCleartextTrafficPermitted.overload().implementation = function () {
            return true;
        };
    } catch (e) { console.log("[-] NetworkSecurityPolicy: " + e); }

    // ── 6. Conscrypt OpenSSLSocketImpl verify ────────────────────────────────
    try {
        var OpenSSLSocketImpl = Java.use("com.android.org.conscrypt.OpenSSLSocketImpl");
        OpenSSLSocketImpl.verifyCertificateChain.implementation = function (certRefs, authMethod) {
            console.log("[+] OpenSSLSocketImpl.verifyCertificateChain → bypassed");
        };
    } catch (e) { console.log("[-] OpenSSLSocketImpl: " + e); }

    console.log("[*] All SSL bypass hooks installed ✓");
});
