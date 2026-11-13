"use strict";
/*
 * TCL vOS Host Port v1 — renderer-independent game I/O interface.
 *
 * Any platform (Android, desktop, Godot Web, or future WASM host) may
 * implement state, command, and selectProfile. The renderer NEVER owns
 * the universal τ wallet. In Android TCLNative is only a platform adapter.
 *
 * No Java, Android, network, blockchain, or external identity dependency.
 */
(function (root) {
  const VERSION = "tcl-vos-host-v1";
  const CALLS = Object.freeze(["state", "command", "selectProfile"]);
  const REQUEST_ID = /^[A-Za-z0-9_-]{6,48}$/;

  class HostPort {
    constructor(transport) {
      if (!transport || (typeof transport !== "object" &&
                          typeof transport !== "function")) {
        throw new TypeError("A game-host transport must be provided");
      }
      for (const name of CALLS) {
        if (typeof transport[name] !== "function") {
          throw new TypeError("Game-host method unavailable: " + name);
        }
      }
      this.transport = transport;
    }

    invoke(method, ...args) {
      if (!CALLS.includes(method)) {
        throw new Error("Unrecognized host capability");
      }
      if (method === "command") {
        if (args.length !== 4 || typeof args[0] !== "string" ||
            typeof args[1] !== "string" || !Number.isInteger(args[2]) ||
            typeof args[3] !== "string" || !REQUEST_ID.test(args[3])) {
          throw new TypeError("Invalid game command envelope");
        }
      } else if (method === "selectProfile" &&
                 (args.length !== 1 || typeof args[0] !== "string" ||
                  !/^[A-Za-z0-9_-]{2,24}$/.test(args[0]))) {
        throw new TypeError("Invalid local profile");
      } else if (method === "state" && args.length !== 0) {
        throw new TypeError("State has no arguments");
      }

      const raw = this.transport[method](...args);
      if (typeof raw !== "string" || raw.length > 131072) {
        throw new Error("Malformed or oversized host response");
      }
      const result = JSON.parse(raw);
      if (result === null || typeof result !== "object" ||
          Array.isArray(result) || typeof result.ok !== "boolean") {
        throw new Error("Invalid host response envelope");
      }
      // The adapter accepts no arbitrary mint operation: the host itself
      // must also validate registered actions and account invariants.
      return result;
    }

    observeInteraction() {
      if (typeof this.transport.observeInteraction === "function") {
        this.transport.observeInteraction(); // advisory diagnostic, no gate
      }
    }
  }

  root.VOSHost = Object.freeze({
    version: VERSION,
    connect() {
      const transport = root.TCLHostPort || root.TCLNative;
      return transport ? new HostPort(transport) : null;
    },
    create(transport) { return new HostPort(transport); }
  });
})(globalThis);
