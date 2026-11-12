package com.tcl.vos;

import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * JVM lifecycle host for the isolated local TCL vOS simulation service.
 *
 * Uses ProcessBuilder with a fixed argument vector: no shell evaluation,
 * arbitrary executable upload, or networking beyond the Python loopback app.
 * This is a JVM management adapter, NOT a JVM implementation of consensus,
 * the Python game logic, or an audited WASM plugin engine.
 */
public final class TclVosHost {
    private TclVosHost() {}

    public static void main(String[] args) throws InterruptedException, IOException {
        int port = 8765;
        String save = Path.of(System.getProperty("user.home"), ".local", "share",
                              "tcl-vos", "civitas99.json").toString();
        for (int i = 0; i < args.length; ++i) {
            switch (args[i]) {
                case "--port":
                    if (++i == args.length) throw new IllegalArgumentException("--port needs value");
                    port = Integer.parseInt(args[i]);
                    break;
                case "--save":
                    if (++i == args.length) throw new IllegalArgumentException("--save needs value");
                    save = args[i];
                    break;
                case "--help":
                    System.out.println("TCL vOS JVM host: [--port 8765] [--save /path/session.json]");
                    return;
                default:
                    throw new IllegalArgumentException("Unsupported host option: " + args[i]);
            }
        }
        if (port < 1 || port > 65535) throw new IllegalArgumentException("invalid loopback port");
        String python = System.getenv().getOrDefault("TCL_VOS_PYTHON", "python3");
        List<String> command = new ArrayList<>(List.of(
                python, "-m", "tcl.vos", "serve", "--port", Integer.toString(port),
                "--save", save));
        Process server = new ProcessBuilder(command).inheritIO().start();
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            if (server.isAlive()) server.destroy();
        }));
        int code = server.waitFor();
        if (code != 0) throw new IllegalStateException("TCL vOS backend exited " + code);
    }
}

