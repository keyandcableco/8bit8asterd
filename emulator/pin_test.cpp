// Pinned voices (PIN:): a channel given a voice of its own keeps it, whatever
// else is playing; it glides from its own last note; legato carries the
// envelope across a note change. Reads the voice map from DIAG and the chips'
// registers, so it checks what the firmware actually does.
//
//   g++ -std=c++17 -O2 -x c++ -Iemulator -I. -Wno-narrowing \
//       emulator/host.cpp emulator/pin_test.cpp -o /tmp/pintest && /tmp/pintest
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern "C" {
  void emu_init(double rate);
  void emu_render(float *out, int n);
  void emu_note_on(int ch, int note, int vel);
  void emu_note_off(int ch, int note);
  void emu_send_line(const char *line);
  const char *emu_read_lines();
  int emu_reg(int chip, int reg);
}

static const int RATE = 44100;
static int failures = 0;
static void check(bool ok, const char *what, const std::string &detail = "") {
  printf("  %s %s%s%s\n", ok ? "ok  " : "FAIL", what, detail.empty() ? "" : "  -- ", detail.c_str());
  if (!ok) failures++;
}
static void render(double s) { std::vector<float> b((size_t)(s * RATE) + 1); emu_render(b.data(), (int)b.size() - 1); }
static std::string say(const char *line) { emu_send_line(line); render(0.002); return emu_read_lines(); }

// voice -> note it holds (-1 free or releasing note, -2 drum), from DIAG
static std::vector<int> voiceMap() {
  std::string s = say("DIAG");
  size_t at = s.find("DIAG ");
  std::vector<int> m(9, -1);
  if (at == std::string::npos) return m;
  const char *p = s.c_str() + at + 5;
  for (int i = 0; i < 9; i++) {
    int v; char what[16] = {0};
    if (sscanf(p, "%d:c%*d/%15[^/]", &v, what) < 2) break;
    if (what[0] == 'n') m[v] = atoi(what + 1);
    else if (!strcmp(what, "perc")) m[v] = -2;
    p = strchr(p, ' '); if (!p) break; p++;
  }
  return m;
}
static std::string show(const std::vector<int> &m) { std::string s; for (int x : m) s += std::to_string(x) + " "; return s; }

// voice v is channel v/3 of chip v%3
static int period(int v) { int c = v % 3, ch = v / 3; return emu_reg(c, ch * 2) | ((emu_reg(c, ch * 2 + 1) & 15) << 8); }
static int amp(int v) { return emu_reg(v % 3, 8 + v / 3) & 15; }

int main() {
  emu_init(RATE); render(0.05); emu_read_lines();
  say("MPE:1");

  // ---- a channel's voice is its own ----
  std::string r = say("PIN:1:0");  r += say("PIN:2:3"); r += say("PIN:3:6"); r += say("PIN:4:1");
  check(r.find("PIN:75") != std::string::npos, "four channels pinned; the reply is the mask of pinned voices", r);
  emu_note_on(1, 48, 100); emu_note_on(2, 55, 100); emu_note_on(3, 64, 100); emu_note_on(4, 67, 100);
  render(0.05);
  auto m = voiceMap();
  check(m[0] == 48 && m[3] == 55 && m[6] == 64 && m[1] == 67, "each chord voice lands on its pinned voice", show(m));

  // the strings and the drums flood the rest: eleven notes and a kick for five free voices
  for (int i = 0; i < 11; i++) emu_note_on(5 + (i % 11 == 4 ? 10 : i % 11), 72 + i, 100);
  emu_note_on(9, 36, 110); emu_note_on(9, 38, 110);
  render(0.05);
  m = voiceMap();
  check(m[0] == 48 && m[3] == 55 && m[6] == 64 && m[1] == 67, "however much else plays, nothing takes a pinned voice", show(m));

  // a chord change: the line moves on its own voice
  emu_note_off(1, 48); emu_note_on(1, 50, 100);
  emu_note_off(4, 67); emu_note_on(4, 65, 100);
  render(0.05);
  m = voiceMap();
  check(m[0] == 50 && m[1] == 65, "the next chord's notes take the same voices", show(m));
  for (int i = 0; i < 11; i++) emu_note_off(5 + (i % 11 == 4 ? 10 : i % 11), 72 + i);
  render(0.3);

  // ---- glide from its own last note ----
  emu_send_line("P:49:60"); render(0.01);                 // glide
  emu_note_off(1, 50); emu_note_on(1, 48, 100); render(0.3);   // the bass settles on C
  int pC = period(0);
  emu_note_on(6, 96, 100); render(0.01);                  // a high unpinned note starts last
  emu_note_off(1, 48); emu_note_on(1, 50, 100); render(0.004);
  int pStart = period(0);
  render(0.6);
  int pD = period(0);
  check(abs(pStart - pC) < abs(pStart - pD) * 4 && pStart > pD, "a pinned voice glides from its own last note, not from the last note played", "C " + std::to_string(pC) + ", starts at " + std::to_string(pStart) + ", D " + std::to_string(pD));
  emu_note_off(6, 96);
  emu_send_line("P:49:0"); render(0.3);

  // ---- legato ----
  auto restrike = [&](const char *pin) {
    say(pin);
    emu_note_on(1, 60, 100); render(0.6);                 // into sustain
    int held = amp(0);
    emu_note_off(1, 60); emu_note_on(1, 62, 100); render(0.003);
    int after = amp(0);
    emu_note_off(1, 62); render(0.6);
    return std::make_pair(held, after);
  };
  auto plain = restrike("PIN:1:0");
  check(plain.second < plain.first / 2, "without legato a chord change restrikes the voice", std::to_string(plain.first) + " -> " + std::to_string(plain.second));
  auto leg = restrike("PIN:1:0:255:1");
  check(leg.first > 0 && leg.second >= leg.first - 1, "with legato the voice carries on into the next note", std::to_string(leg.first) + " -> " + std::to_string(leg.second));

  // ---- off ----
  r = say("PIN:off");
  check(r.find("PIN:0") != std::string::npos, "PIN:off frees every voice", r);
  for (int i = 0; i < 9; i++) emu_note_on(5, 60 + i, 100);
  render(0.05);
  m = voiceMap();
  int used = 0; for (int x : m) if (x >= 0) used++;
  check(used == 9, "and all nine are dealt out again", show(m));

  printf(failures ? "pin test: %d FAILED\n" : "pin test: all passed\n", failures);
  return failures ? 1 : 0;
}
