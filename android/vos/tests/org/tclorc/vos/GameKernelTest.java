package org.tclorc.vos;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;

/** Dependency-free executable gate for cross-game currency and replay safety. */
public final class GameKernelTest {
    private static int assertions = 0;
    private static void yes(boolean value, String message) {
        assertions++;
        if (!value) throw new AssertionError(message);
    }

    private static void rejects(Runnable call, String message) {
        try {call.run();} catch (IllegalArgumentException expected) {assertions++; return;}
        throw new AssertionError(message);
    }

    private static String id(String name) {return "game-" + name + "-12345";}

    private static void sharedCreditTransfer() throws Exception {
        File dir = Files.createTempDirectory("tclvos-market-").toFile();
        File file = new File(dir, "wallet.journal");
        GameKernel kernel = new GameKernel(file);
        yes(kernel.snapshot().credits == 500, "initial universal wallet");
        GameKernel.Snapshot earned = kernel.dispatch("civitas", "shift", 0, id("shift"));
        yes(earned.credits == 516 && earned.inventory[0] == 5, "Civitas wages must mint only verified shift");
        yes(kernel.dispatch("nightdrive", "tune", 0, id("upgrade")).credits == 416,
                "credits from Civitas must be spendable in Night Drive");
        yes(kernel.snapshot().turbo, "turbo installed");
        // Turbo moves two tiles when unobstructed, reaching the first civic checkpoint.
        for (int n=0;n<5;n++) kernel.dispatch("nightdrive", "move", 1, id("east" + n));
        for (int n=0;n<3;n++) kernel.dispatch("nightdrive", "move", 2, id("south" + n));
        yes(kernel.snapshot().deliveries == 1, "must finish first open-city delivery");
        yes(kernel.snapshot().credits == 461, "night drive delivery awards shared wallet");
        GameKernel.Snapshot bought = kernel.dispatch("civitas", "buy", 2, id("marketbuy"));
        yes(bought.credits == 443 && bought.inventory[2] == 4,
                "Night Drive earned credits must be spendable in Civitas");
        yes(bought.issued - bought.sunk == bought.credits, "universal wallet audit equality");
        yes(bought.eventCount == 11, "ledger must contain 11 validated commands");
        GameKernel.Snapshot dup = kernel.dispatch("civitas", "buy", 2, id("marketbuy"));
        yes(dup.credits == bought.credits && dup.eventCount == bought.eventCount,
                "duplicate request ID must be idempotent");
        rejects(() -> {try {kernel.dispatch("civitas", "sell", 2, id("marketbuy"));}
                    catch (IOException e) {throw new RuntimeException(e);}}, "same ID different command rejected");
        GameKernel restored = new GameKernel(file);
        yes(restored.snapshot().credits == 443, "shared wallet persists after restart");
        yes(restored.snapshot().deliveries == 1 && restored.snapshot().turbo,
                "all game-specific progress persists with the same wallet");
        yes(restored.snapshot().hash.equals(kernel.snapshot().hash), "replay must match digest");
        yes(restored.dispatch("civitas", "buy", 2, id("marketbuy")).credits == 443,
                "idempotency keys survive app restart");
        // No mint method, separate game wallet or arbitrary transfer command exists.
        rejects(() -> {try {restored.dispatch("other-game", "credit", 10000000, id("attack"));}
                    catch (IOException e) {throw new RuntimeException(e);}}, "unknown game must not mint");
        rejects(() -> {try {restored.dispatch("nightdrive", "move", 100, id("attack2"));}
                    catch (IOException e) {throw new RuntimeException(e);}}, "invalid direction must fail");
        yes(restored.snapshot().credits == 443, "failed requests never change balance");
        // Tampering with on-disk balance is detected on load; never silently reset a user's save.
        String log = Files.readString(file.toPath());
        Files.writeString(file.toPath(), log.replace("|443|", "|999|"), StandardCharsets.UTF_8);
        try {new GameKernel(file); throw new AssertionError("tamper undetected");}
        catch (IOException expected) {assertions++;}
    }

    private static void localProfilesAndLimits() throws Exception {
        GameKernel alice = new GameKernel(null), bob = new GameKernel(null);
        alice.dispatch("civitas", "shift", 1, id("aaashift"));
        yes(alice.snapshot().credits == 516 && bob.snapshot().credits == 500,
                "separate local profiles do not share each other's wallets");
        rejects(() -> {try {alice.dispatch("civitas", "shift", 0, id("repeat"));}
                    catch (IOException e) {throw new RuntimeException(e);}}, "one shift per turn");
        rejects(() -> {try {alice.dispatch("civitas", "buy", -1, id("badbuy"));}
                    catch (IOException e) {throw new RuntimeException(e);}}, "invalid market resource");
        rejects(() -> {try {alice.dispatch("nightdrive", "tune", 9, id("badopt"));}
                    catch (IOException e) {throw new RuntimeException(e);}}, "invalid upgrade");
        alice.dispatch("nightdrive", "move", 1, id("goodmove"));
        rejects(() -> {try {alice.dispatch("nightdrive", "move", 2, id("noblock"));}
                    catch (IOException e) {throw new RuntimeException(e);}}, "off-road building collisions must reject");
    }

    private static void deterministicConcurrency() throws Exception {
        GameKernel kernel = new GameKernel(null);
        int threads = 4, each = 30;
        CountDownLatch start = new CountDownLatch(1), done = new CountDownLatch(threads);
        AtomicReference<Throwable> problem = new AtomicReference<>();
        for (int i=0; i<threads; i++) {
            int worker=i;
            new Thread(() -> {
                try {
                    start.await();
                    for(int j=0;j<each;j++)
                        kernel.dispatch("civitas", "next-turn", 0, id("turn"+worker+"_"+j));
                } catch (Throwable e) {problem.compareAndSet(null, e);}
                finally {done.countDown();}
            }).start();
        }
        start.countDown(); done.await();
        if(problem.get()!=null) throw new AssertionError("Concurrent action failure",problem.get());
        yes(kernel.snapshot().eventCount == threads*each, "all distinct concurrent commands recorded");
        yes(kernel.snapshot().turn == 121, "all turn advances serializable");
        yes(kernel.snapshot().credits == 500, "turn operations do not mint money");
    }

    private static void entropyIsNeverEnforcement() {
        EntropyMeter meter=new EntropyMeter();
        for(int i=0;i<40;i++) meter.record(i*120L);
        yes(meter.sampleCount()==32,"bounded 32-sample window");
        yes(meter.normalized()==0.0,"uniform intervals low diversity");
        yes(meter.label().contains("remains unrestricted"),"low entropy must never ban legitimate automation");
    }

    public static void main(String[] args) throws Exception {
        sharedCreditTransfer();
        localProfilesAndLimits();
        deterministicConcurrency();
        entropyIsNeverEnforcement();
        System.out.println("TCL vOS native wallet / cross-game economy / integrity PASS ("+assertions+" assertions)");
    }
}
