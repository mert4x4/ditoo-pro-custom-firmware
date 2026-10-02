/* Host test for remote.c: runs the firmware logic on a PC with the stock
 * functions and variables replaced by fakes.
 *
 *   cc -std=c11 -Wall -Werror -o /tmp/host_test tests/host_test.c && /tmp/host_test
 *
 * (run from firmware/remote/; build.py does not use this file) */
#define HOST_TEST
#include <stdio.h>
#include <string.h>

struct state;
struct state *S;
#include "../remote.c"

/* ---- fakes ------------------------------------------------------------- */

static struct state state_ram;
static unsigned now_ms, fake_sp, interrupts, scheduler, reply_ok, stack_hwm_words;
static unsigned char shadow[8];
static unsigned char tcb_device[0x54], tcb_other[0x54];

unsigned char *stock_screen_shadow = shadow;
volatile unsigned stock_battery_level;
volatile unsigned char stock_usb_power, stock_charge_pin, stock_ble_connected;
void *volatile stock_current_task;
void *volatile stock_device_task;

unsigned cpu_sp(void) { return fake_sp; }
unsigned cpu_interrupts_on(void) { return interrupts; }
unsigned stock_ticks(void) { return now_ms; }
unsigned stock_scheduler_state(void) { return scheduler; }
unsigned stock_stack_high_water(void *task) { return task == tcb_device ? stack_hwm_words : 0; }

static unsigned presented;
unsigned stock_screen(const void *frame) { (void)frame; ++presented; return 1; }

/* Every stock_reply call, in order. */
static struct { unsigned context, opcode, size; unsigned char data[80]; } sent[64];
static unsigned sent_count, reply_calls;
static void (*during_reply)(void);   /* runs inside the next stock_reply, like a preempting task */

unsigned stock_reply(unsigned context, unsigned opcode, const void *data, unsigned size) {
    ++reply_calls;
    if (during_reply) {
        void (*fn)(void) = during_reply;
        during_reply = 0;
        fn();
    }
    if (!reply_ok) return 0;
    if (sent_count < 64) {
        sent[sent_count].context = context;
        sent[sent_count].opcode = opcode;
        sent[sent_count].size = size;
        memcpy(sent[sent_count].data, data, size < 80 ? size : 80);
        ++sent_count;
    }
    return 1;
}

/* ---- helpers ------------------------------------------------------------ */

static int failures, checks;
#define CHECK(cond) do { ++checks; if (!(cond)) { ++failures; \
    printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

static const unsigned STACK_START = 0x20020000;

static void reset(void) {
    memset(&state_ram, 0x5a, sizeof state_ram);   /* garbage: magic must not match */
    S = &state_ram;
    now_ms = 1000;
    interrupts = 1;
    scheduler = 2;
    reply_ok = 1;
    stack_hwm_words = 300;
    stock_ble_connected = 1;
    stock_battery_level = 5;
    stock_usb_power = 1;
    stock_charge_pin = 0;
    memset(tcb_device, 0, sizeof tcb_device);
    memcpy(tcb_device + TCB_STACK_START, &STACK_START, 4);
    stock_current_task = stock_device_task = tcb_device;
    fake_sp = STACK_START + 1400;                 /* ~1400 free, like the real hook */
    memset(shadow, 0, sizeof shadow);
    sent_count = reply_calls = presented = 0;
    during_reply = 0;
}

/* Send a 0xF0 command; length includes the two checksum bytes like the stock path. */
static const unsigned char *command(unsigned sub, int arg) {
    unsigned char data[8] = { 0xf0, sub, (unsigned char)arg, 0, 0 };
    unsigned before = sent_count;
    remote_command(0x1234, data, arg >= 0 ? 5 : 4);
    return sent_count > before ? sent[sent_count - 1].data : NULL;
}

static unsigned key(unsigned k, unsigned event) { return remote_key(k | event << 16); }
static unsigned idle_scan(void) { return remote_key(0xff); }
static unsigned last_is_push(void) { return sent_count && sent[sent_count - 1].data[0] == SUB_PUSH; }
static unsigned u16(const unsigned char *p) { return p[0] | p[1] << 8; }

/* ---- tests -------------------------------------------------------------- */

static void test_passthrough_outside_modes(void) {
    reset();
    CHECK(key(1, 1) == (1 | 1 << 16));            /* no state yet */
    command(0, -1);                               /* poll, still no state */
    CHECK(key(1, 1) == (1 | 1 << 16));
    CHECK(sent_count == 1 && sent[0].data[2] == 0);
}

static void test_listen_push(void) {
    reset();
    const unsigned char *r = command(7, 1);
    CHECK(r && r[0] == 7 && r[1] == 0 && r[2] == 2);
    CHECK(key(2, 1) == 0xff);                     /* hidden from the stock UI */
    CHECK(last_is_push());
    const unsigned char *p = sent[sent_count - 1].data;
    CHECK(sent[sent_count - 1].size == 7 && sent[sent_count - 1].opcode == 0xf0);
    CHECK(sent[sent_count - 1].context == 0x1234);
    CHECK(p[1] == 0 && p[2] == 2 && p[3] == 0 && p[4] == 1 && p[5] == 2 && p[6] == 1);
    r = command(0, -1);
    CHECK(r[4] == 0);                             /* nothing left in the queue */
    CHECK(S->pushed == 1);
}

static void test_drawing_mode_pushes_too(void) {
    reset();
    const unsigned char *r = command(1, -1);
    CHECK(r[2] == 1 && presented == 1);
    CHECK(key(0, 1) == 0xff && last_is_push() && sent[sent_count - 1].data[2] == 1);
}

static void test_push_failure_falls_back_to_queue(void) {
    reset();
    command(7, 1);
    reply_ok = 0;
    key(3, 1);                                    /* stock queue refuses */
    reply_ok = 1;
    CHECK(S->push_failed == 1 && S->key_write - S->key_read == 1);
    unsigned n = sent_count;
    key(3, 2);                                    /* queue not empty: must not overtake */
    CHECK(sent_count == n);
    const unsigned char *r = command(0, -1);
    CHECK(r[4] == 2 && r[5] == 3 && r[6] == 1 && r[7] == 3 && r[8] == 2);
    key(3, 1);                                    /* queue empty again: push resumes */
    CHECK(last_is_push());
}

static void test_no_push_when_unsafe(void) {
    const char *cases[] = { "interrupts off", "scheduler suspended", "low stack",
                            "other task", "link down" };
    for (unsigned c = 0; c < sizeof cases / sizeof cases[0]; ++c) {
        reset();
        command(7, 1);
        unsigned n = sent_count;
        if (c == 0) interrupts = 0;
        if (c == 1) scheduler = 0;
        if (c == 2) fake_sp = STACK_START + PUSH_MIN_STACK - 1;
        if (c == 3) stock_current_task = tcb_other;
        if (c == 4) { stock_ble_connected = 0; S->over_ble = 0; }  /* e.g. host on SPP */
        key(4, 1);
        if (sent_count != n) printf("  case: %s\n", cases[c]);
        CHECK(sent_count == n);
        CHECK(S->key_write - S->key_read == 1);   /* kept for the next reply */
        if (c == 2) CHECK(S->low_stack == 1 && S->min_free == PUSH_MIN_STACK - 1);
        if (c == 0 || c == 1 || c == 4) CHECK(S->blocked == 1 && S->low_stack == 0);
    }
    reset();                                      /* exactly at the limit pushes */
    command(7, 1);
    fake_sp = STACK_START + PUSH_MIN_STACK;
    key(4, 1);
    CHECK(last_is_push() && S->low_stack == 0);
}

static void test_disconnect_ends_capturing(void) {
    reset();
    command(1, -1);
    command(7, 1);
    CHECK(S->active && S->listen && S->over_ble);
    stock_ble_connected = 0;
    idle_scan();                                  /* next key scan loop */
    CHECK(!S->active && !S->listen);
    CHECK(shadow[4] == 0xff);                     /* stock screen will be redrawn */
    CHECK(key(1, 1) == (1 | 1 << 16));            /* keys back to the stock UI */
    stock_ble_connected = 1;                      /* reconnect: stays off */
    idle_scan();
    CHECK(!S->active && !S->listen);
}

static void test_disconnect_check_only_for_ble_hosts(void) {
    reset();
    stock_ble_connected = 0;                      /* command arrived another way */
    command(7, 1);
    CHECK(S->listen && !S->over_ble);
    idle_scan();
    CHECK(S->listen);                             /* only the watchdog applies */
    now_ms += WATCHDOG_MS + 1;
    idle_scan();
    CHECK(!S->listen);
}

static void test_watchdog(void) {
    reset();
    command(7, 1);
    now_ms += 45000;                              /* host keepalive is every ~30 s */
    idle_scan();
    CHECK(S->listen);
    command(0, -1);
    now_ms += WATCHDOG_MS;
    idle_scan();
    CHECK(S->listen);                             /* exactly 60 s: still on */
    command(6, -1);                               /* any 0xF0 command keeps it alive */
    now_ms += WATCHDOG_MS;
    idle_scan();
    CHECK(S->listen);
    now_ms += 1;
    idle_scan();
    CHECK(!S->listen);
    /* the screen path runs the watchdog too */
    command(1, -1);
    now_ms += WATCHDOG_MS + 1;
    CHECK(remote_screen_allowed((void *)1) == 1 && !S->active);
}

static void test_escape(void) {
    reset();
    command(7, 1);
    key(9, 1);                                    /* down at t */
    now_ms += 3999;
    key(9, 3);                                    /* long at t + 3999: not yet */
    CHECK(S->listen);
    now_ms += 1;
    unsigned n = sent_count;
    CHECK(key(9, 4) == 0xff);                     /* repeat at t + 4000: escape */
    CHECK(!S->listen);
    CHECK(sent_count == n + 1 && last_is_push());
    const unsigned char *p = sent[sent_count - 1].data;
    CHECK(sent[sent_count - 1].size == 5 && p[2] == 0 && p[4] == 0);
    CHECK(key(9, 4) == 0xff);                     /* rest of the press stays hidden */
    CHECK(key(9, 5) == 0xff);                     /* release after long, hidden */
    CHECK(key(9, 1) == (9 | 1 << 16));            /* next press goes to the stock UI */
}

static void test_escape_ignores_key_held_before_start(void) {
    reset();
    now_ms = 50000;
    command(7, 1);                                /* key already held, its down came before */
    now_ms += 3000;
    key(1, 4);
    CHECK(S->listen);
    now_ms += 1000;
    key(1, 4);                                    /* 4 s since listening started */
    CHECK(!S->listen);
}

static void test_queue_limit(void) {
    reset();
    command(7, 1);
    reply_ok = 0;
    for (unsigned i = 0; i < QUEUE_SIZE + 3; ++i) key(1, 1);
    reply_ok = 1;
    CHECK(S->key_write - S->key_read == QUEUE_SIZE && S->dropped == 3);
    const unsigned char *r = command(0, -1);
    CHECK(r[3] == 3 && r[4] == REPLY_KEYS);
    r = command(0, -1);
    CHECK(r[4] == QUEUE_SIZE - REPLY_KEYS);
}

static void press_right(void) { key(3, 1); }

/* A key pressed while a reply with older keys is on its way must not be
 * pushed ahead of that reply. */
static void test_push_cannot_overtake_reply(void) {
    reset();
    command(7, 1);
    reply_ok = 0;
    key(1, 1);                                    /* queued */
    reply_ok = 1;
    during_reply = press_right;                   /* pressed while the poll reply is built */
    unsigned n = sent_count;
    const unsigned char *r = command(0, -1);
    CHECK(sent_count == n + 1);                   /* only the reply, no push */
    CHECK(r[4] == 1 && r[5] == 1);
    CHECK(S->key_write - S->key_read == 1);       /* right is queued for the next reply */
    r = command(0, -1);
    CHECK(r[4] == 1 && r[5] == 3 && r[6] == 1);
}

/* A reply the stock queue refuses keeps its keys for the next one. */
static void test_refused_reply_keeps_keys(void) {
    reset();
    command(7, 1);
    reply_ok = 0;
    key(2, 1);
    command(0, -1);                               /* refused */
    CHECK(S->key_write - S->key_read == 1);
    reply_ok = 1;
    const unsigned char *r = command(0, -1);
    CHECK(r[4] == 1 && r[5] == 2);
    CHECK(S->key_write == S->key_read);
}

/* Warm reboot: RAM keeps our state (magic valid, modes on), the tick counter
 * restarts near 0 and BLE is down. The first key scan must end capturing and
 * the keys must go to the stock UI. */
static void test_warm_reboot(void) {
    reset();
    now_ms = 900000;
    command(1, -1);
    command(7, 1);
    CHECK(S->active && S->listen);
    now_ms = 50;                                  /* ticks restarted */
    stock_ble_connected = 0;
    unsigned n = sent_count;
    CHECK(key(0, 1) == (0 | 1 << 16));
    CHECK(!S->active && !S->listen && sent_count == n);
    reset();                                      /* same, but BLE already up again */
    now_ms = 900000;
    command(7, 1);
    now_ms = 50;
    CHECK(key(0, 1) == (0 | 1 << 16) && !S->listen);
}

static void test_stats(void) {
    reset();
    command(7, 1);
    key(1, 1);
    key(1, 2);
    fake_sp = STACK_START + 500;
    key(1, 1);
    const unsigned char *r = command(9, -1);
    CHECK(sent[sent_count - 1].size == 5 + 2 + 12);  /* the queued key, then the stats */
    CHECK(r[0] == 9 && r[4] == 1 && r[5] == 1 && r[6] == 1);
    const unsigned char *s = r + 7;
    CHECK(u16(s) == 2 && u16(s + 2) == 1 && u16(s + 4) == 0);
    CHECK(u16(s + 6) == 500);
    CHECK(u16(s + 8) == 1200);                    /* 300 words */
    CHECK(u16(s + 10) == 0);
    reset();                                      /* before any push */
    r = command(9, -1);
    CHECK(u16(r + 5 + 6) == 0xffff && u16(r + 5) == 0);
}

static void test_battery_unchanged(void) {
    reset();
    const unsigned char *r = command(6, -1);
    CHECK(sent[sent_count - 1].size == 8 && r[5] == 5 && r[6] == 1 && r[7] == 0);
}

static void test_leave_drawing_keeps_listening(void) {
    reset();
    command(7, 1);
    command(1, -1);
    const unsigned char *r = command(2, -1);
    CHECK(r[2] == 2);
    key(2, 1);
    CHECK(last_is_push() && sent[sent_count - 1].data[2] == 2);
}

int main(void) {
    test_passthrough_outside_modes();
    test_listen_push();
    test_drawing_mode_pushes_too();
    test_push_failure_falls_back_to_queue();
    test_no_push_when_unsafe();
    test_disconnect_ends_capturing();
    test_disconnect_check_only_for_ble_hosts();
    test_watchdog();
    test_escape();
    test_escape_ignores_key_held_before_start();
    test_queue_limit();
    test_push_cannot_overtake_reply();
    test_refused_reply_keeps_keys();
    test_warm_reboot();
    test_stats();
    test_battery_unchanged();
    test_leave_drawing_keeps_listening();
    printf("%d checks, %d failed\n", checks, failures);
    return failures != 0;
}
