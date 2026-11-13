package org.tclorc.vos;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Standalone, local-only game operating system economy.
 *
 * ONE universal TCL vOS credit balance is shared by every game attached to
 * this kernel. Every earning/spending operation is a validated host game verb,
 * not a JavaScript mint() callback. Ledger data is an append-only, replayed
 * hash-linked command log with atomic persistence. No financial ledger or
 * external token integration is available in this MVP.
 */
public final class GameKernel {
    public static final int WORLD_SIZE = 24;
    public static final long START_CREDITS = 500L;
    public static final int[] MARKET_BUY = {8, 12, 18};
    public static final int[] MARKET_SELL = {5, 8, 12};
    public static final String[] RESOURCES = {"energy", "water", "alloy"};
    private static final String HEADER = "TCL-VOS-WALLET-1";
    private static final String GENESIS = "TCL-vOS-local-genesis-v1";
    private static final int MAX_EVENTS = 10000;
    private static final int MAX_FILE_BYTES = 2 * 1024 * 1024;
    private static final int[][] GOALS = {{12, 7}, {17, 12}, {7, 17}, {2, 12}, {12, 22}};

    public static final class Snapshot {
        public final long credits, issued, sunk;
        public final int[] inventory;
        public final int turn, lastWorkTurn, x, y, heading, goalX, goalY, fuel, deliveries;
        public final boolean turbo, paint;
        public final int eventCount;
        public final String hash, message;
        public final long delta;
        public final List<String> recent;

        private Snapshot(State s, List<Event> log) {
            credits = s.credits; issued = s.issued; sunk = s.sunk;
            inventory = s.inventory.clone(); turn = s.turn; lastWorkTurn = s.lastWorkTurn;
            x = s.x; y = s.y; heading = s.heading;
            int[] goal = GOALS[s.deliveries % GOALS.length];
            goalX = goal[0]; goalY = goal[1]; fuel = s.fuel; deliveries = s.deliveries;
            turbo = s.turbo; paint = s.paint; eventCount = log.size();
            hash = log.isEmpty() ? GENESIS : log.get(log.size() - 1).hash;
            message = s.message; delta = s.delta;
            List<String> lines = new ArrayList<>();
            for (int i = Math.max(0, log.size() - 8); i < log.size(); i++) {
                Event e = log.get(i);
                lines.add(e.game + " / " + e.action + " · " + e.balance + " τ");
            }
            recent = Collections.unmodifiableList(lines);
        }
    }

    private static final class State {
        long credits = START_CREDITS;
        long issued = START_CREDITS;
        long sunk = 0L;
        int[] inventory = {3, 3, 3};
        int turn = 1, lastWorkTurn = 0, x = 2, y = 2, heading = 1;
        int fuel = 60, deliveries = 0;
        boolean turbo = false, paint = false;
        String message = "Welcome to TCL vOS. Every game shares one wallet.";
        long delta = 0L;

        State copy() {
            State n = new State();
            n.credits = credits; n.issued = issued; n.sunk = sunk;
            n.inventory = inventory.clone(); n.turn = turn; n.lastWorkTurn = lastWorkTurn;
            n.x = x; n.y = y; n.heading = heading; n.fuel = fuel;
            n.deliveries = deliveries; n.turbo = turbo; n.paint = paint;
            n.message = message; n.delta = delta;
            return n;
        }
    }

    private static final class Event {
        int seq, arg;
        String requestId, game, action, hash;
        long balance, issued, sunk;

        String payload() {
            return seq + "|" + requestId + "|" + game + "|" + action + "|" +
                arg + "|" + balance + "|" + issued + "|" + sunk;
        }

        String serialize() { return payload() + "|" + hash; }
    }

    private final File store;
    private State state = new State();
    private final ArrayList<Event> events = new ArrayList<>();
    private final Set<String> requests = new HashSet<>();
    private final Map<String, String> requestPayloads = new HashMap<>();

    public GameKernel(File path) throws IOException {
        this.store = path;
        if (store != null && store.isFile()) load();
    }

    public synchronized Snapshot snapshot() { return new Snapshot(state, events); }

    public synchronized Snapshot dispatch(String game, String action, int arg, String requestId)
            throws IOException {
        if (requestId == null || !requestId.matches("[A-Za-z0-9_-]{6,48}")) {
            throw new IllegalArgumentException("Invalid local command identifier");
        }
        if (requests.contains(requestId)) {
            String prior = requestPayloads.get(requestId);
            if (!prior.equals(game + "/" + action + "/" + arg)) {
                throw new IllegalArgumentException("Command ID reused with different input");
            }
            return snapshot(); // Duplicate delivery cannot spend/earn twice.
        }
        if (events.size() >= MAX_EVENTS) {
            throw new IllegalStateException("Local save full; export before further play");
        }
        State draft = state.copy();
        apply(draft, game, action, arg);
        assertAccounting(draft);
        Event event = new Event();
        event.seq = events.size() + 1;
        event.requestId = requestId; event.game = game; event.action = action; event.arg = arg;
        event.balance = draft.credits; event.issued = draft.issued; event.sunk = draft.sunk;
        String previous = events.isEmpty() ? GENESIS : events.get(events.size() - 1).hash;
        event.hash = digest(previous + "|" + event.payload());
        ArrayList<Event> pending = new ArrayList<>(events);
        pending.add(event);
        if (store != null) persist(pending); // commit disk first; no RAM mutation on I/O failure
        events.add(event);
        state = draft;
        requests.add(requestId);
        requestPayloads.put(requestId, game + "/" + action + "/" + arg);
        return snapshot();
    }

    private static void earn(State s, long amount) {
        if (amount < 0 || s.credits > Long.MAX_VALUE - amount ||
                s.issued > Long.MAX_VALUE - amount) throw new IllegalArgumentException("Credit overflow");
        s.credits += amount;
        s.issued += amount;
        s.delta += amount;
    }

    private static void spend(State s, long amount) {
        if (amount < 0 || amount > s.credits) throw new IllegalArgumentException("Insufficient TCL vOS credits");
        s.credits -= amount;
        s.sunk += amount;
        s.delta -= amount;
    }

    private static void apply(State s, String game, String action, int arg) {
        s.delta = 0;
        if ("civitas".equals(game)) {
            switch (action == null ? "" : action) {
                case "shift":
                    resource(arg);
                    if (s.lastWorkTurn == s.turn) throw new IllegalArgumentException("Shift already completed this turn");
                    s.inventory[arg] += 2;
                    s.lastWorkTurn = s.turn;
                    earn(s, 16);
                    s.message = "Civitas shift complete: +2 " + RESOURCES[arg] + ", +16 τ universal credits";
                    break;
                case "buy":
                    resource(arg);
                    spend(s, MARKET_BUY[arg]);
                    s.inventory[arg]++;
                    s.message = "Purchased one " + RESOURCES[arg] + " using TCL vOS credits";
                    break;
                case "sell":
                    resource(arg);
                    if (s.inventory[arg] <= 0) throw new IllegalArgumentException("No resource available to sell");
                    s.inventory[arg]--;
                    earn(s, MARKET_SELL[arg]);
                    s.message = "Sold one " + RESOURCES[arg] + "; credits available in every vOS game";
                    break;
                case "next-turn":
                    if (arg != 0) throw new IllegalArgumentException("Invalid turn command");
                    if (s.turn >= 10000) throw new IllegalArgumentException("End of local demo timeline");
                    s.turn++;
                    s.message = "Civitas turn " + s.turn + " started";
                    break;
                default: throw new IllegalArgumentException("Unknown Civitas command");
            }
        } else if ("nightdrive".equals(game)) {
            switch (action == null ? "" : action) {
                case "move":
                    if (arg < 0 || arg > 3) throw new IllegalArgumentException("Unknown driving direction");
                    int[][] vectors = {{0, -1}, {1, 0}, {0, 1}, {-1, 0}};
                    int count = s.turbo ? 2 : 1;
                    if (s.fuel < count) throw new IllegalArgumentException("Out of fuel — refuel using the shared wallet");
                    for (int i = 0; i < count; i++) {
                        int nx = s.x + vectors[arg][0], ny = s.y + vectors[arg][1];
                        if (!road(nx, ny)) {
                            if (i == 0) throw new IllegalArgumentException("Building ahead — drive on roads");
                            break;
                        }
                        s.x = nx; s.y = ny; s.fuel--; s.heading = arg;
                        int[] goal = GOALS[s.deliveries % GOALS.length];
                        if (s.x == goal[0] && s.y == goal[1]) {
                            s.deliveries++;
                            earn(s, 45);
                            s.message = "Delivery complete! +45 τ in the shared TCL vOS wallet";
                        }
                    }
                    if (s.delta == 0) s.message = "Cruising the city · checkpoint marked in gold";
                    break;
                case "refuel":
                    if (arg != 0) throw new IllegalArgumentException("Invalid refuel argument");
                    if (s.fuel == 60) throw new IllegalArgumentException("Fuel tank already full");
                    spend(s, 20);
                    s.fuel = 60;
                    s.message = "Refueled for 20 τ from your shared wallet";
                    break;
                case "tune":
                    if (arg == 0) {
                        if (s.turbo) throw new IllegalArgumentException("Turbo already unlocked");
                        spend(s, 100);
                        s.turbo = true;
                        s.message = "Turbo installed · two road cells per movement";
                    } else if (arg == 1) {
                        if (s.paint) throw new IllegalArgumentException("Neon paint already unlocked");
                        spend(s, 55);
                        s.paint = true;
                        s.message = "Neon paint unlocked for your Night Drive car";
                    } else throw new IllegalArgumentException("Unknown garage upgrade");
                    break;
                default: throw new IllegalArgumentException("Unknown Night Drive command");
            }
        } else {
            throw new IllegalArgumentException("Unknown game (no arbitrary credit minting)");
        }
    }

    public static boolean road(int x, int y) {
        return x >= 0 && y >= 0 && x < WORLD_SIZE && y < WORLD_SIZE && (x % 5 == 2 || y % 5 == 2);
    }

    private static void resource(int index) {
        if (index < 0 || index >= RESOURCES.length) throw new IllegalArgumentException("Unknown market resource");
    }

    private static void assertAccounting(State s) {
        if (s.issued < 0 || s.sunk < 0 || s.issued - s.sunk != s.credits || s.credits < 0)
            throw new IllegalStateException("Universal wallet accounting invariant violated");
        for (int amount : s.inventory) if (amount < 0) throw new IllegalStateException("Negative resource inventory");
        if (!road(s.x, s.y) || s.fuel < 0 || s.fuel > 60) throw new IllegalStateException("Invalid driving state");
    }

    private static String digest(String source) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(source.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(64);
            for (byte b : hash) hex.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
            return hex.toString();
        } catch (NoSuchAlgorithmException ex) { throw new AssertionError(ex); }
    }

    private void persist(List<Event> all) throws IOException {
        StringBuilder out = new StringBuilder(HEADER).append('\n');
        for (Event event : all) out.append(event.serialize()).append('\n');
        byte[] bytes = out.toString().getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_FILE_BYTES) throw new IOException("Save exceeds 2 MiB safety budget");
        File parent = store.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) throw new IOException("Could not create private save directory");
        File tmp = new File(store.getAbsolutePath() + ".pending");
        try {
            try (FileOutputStream stream = new FileOutputStream(tmp, false)) {
                stream.write(bytes);
                stream.getFD().sync();
            }
            Files.move(tmp.toPath(), store.toPath(), StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING);
        } finally { Files.deleteIfExists(tmp.toPath()); }
    }

    private void load() throws IOException {
        if (store.length() > MAX_FILE_BYTES) throw new IOException("Save exceeds 2 MiB safety budget");
        String raw = new String(Files.readAllBytes(store.toPath()), StandardCharsets.UTF_8);
        String[] lines = raw.split("\n", -1);
        if (lines.length == 0 || !HEADER.equals(lines[0]) || lines.length > MAX_EVENTS + 3)
            throw new IOException("Invalid TCL vOS journal header or length");
        String previous = GENESIS;
        State replay = new State();
        for (int i = 1; i < lines.length; i++) {
            if (lines[i].isEmpty() && i == lines.length - 1) break;
            String[] cells = lines[i].split("\\|", -1);
            if (cells.length != 9) throw new IOException("Corrupted journal record");
            try {
                Event e = new Event();
                e.seq = Integer.parseInt(cells[0]); e.requestId = cells[1];
                e.game = cells[2]; e.action = cells[3]; e.arg = Integer.parseInt(cells[4]);
                e.balance = Long.parseLong(cells[5]); e.issued = Long.parseLong(cells[6]);
                e.sunk = Long.parseLong(cells[7]); e.hash = cells[8];
                if (e.seq != events.size() + 1 || !e.requestId.matches("[A-Za-z0-9_-]{6,48}") ||
                    !e.hash.matches("[0-9a-f]{64}") || requests.contains(e.requestId) ||
                    !digest(previous + "|" + e.payload()).equals(e.hash)) {
                    throw new IOException("Journal integrity check failed");
                }
                apply(replay, e.game, e.action, e.arg);
                assertAccounting(replay);
                if (replay.credits != e.balance || replay.issued != e.issued || replay.sunk != e.sunk)
                    throw new IOException("Journal state diverged during replay");
                events.add(e); requests.add(e.requestId);
                requestPayloads.put(e.requestId, e.game + "/" + e.action + "/" + e.arg);
                previous = e.hash;
            } catch (IllegalArgumentException ex) {
                throw new IOException("Invalid or untrusted journal record", ex);
            }
        }
        state = replay;
    }
}
