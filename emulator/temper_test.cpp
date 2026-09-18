#include <cstdint>
#include <cstdio>
#include <vector>
#include <cmath>
#include <string>
extern "C" {
  void emu_init(double); void emu_render(float*,int);
  void emu_note_on(int,int,int); void emu_note_off(int,int);
  void emu_send_line(const char*); const char* emu_read_lines();
  int emu_reg(int,int);
}
// Reads back the tone period the firmware programmed for a held note, which
// is the pitch the chip will actually produce -- no DFT needed.
static int periodForNote(int note, int temperament, int root) {
  emu_init(44100);
  std::vector<float> buf(2048);
  char cmd[64];
  snprintf(cmd, sizeof cmd, "P:%d:%d", 39, temperament); emu_send_line(cmd);
  snprintf(cmd, sizeof cmd, "P:%d:%d", 40, root);          emu_send_line(cmd);
  emu_render(buf.data(), 2048);
  emu_note_on(0, note, 100);
  emu_render(buf.data(), 2048);
  for (int v = 0; v < 9; v++) {
    int lo = emu_reg(v % 3, 0 + (v / 3) * 2), hi = emu_reg(v % 3, 1 + (v / 3) * 2);
    int p = lo | ((hi & 0x0F) << 8);
    int amp = emu_reg(v % 3, 8 + (v / 3));
    if (p > 1 && amp != 0) return p;
  }
  return -1;
}
int main() {
  auto cents = [](double a, double b){ return 1200.0 * log2(b / a); };
  printf("major third C-E, measured from the programmed tone periods\n\n");
  for (auto t : {std::pair<int,const char*>{0,"Equal"}, {2,"Just"}, {1,"Meantone"}, {6,"Vallotti"}}) {
    int pc = periodForNote(48, t.first, 0);     // C3
    int pe = periodForNote(52, t.first, 0);     // E3
    if (pc < 0 || pe < 0) { printf("  %-9s FAILED to read a period\n", t.second); continue; }
    double fc = 1e6 / (16.0 * pc), fe = 1e6 / (16.0 * pe);
    printf("  %-9s C=%7.2fHz E=%7.2fHz  third = %6.1f cents\n", t.second, fc, fe, cents(fc, fe));
  }
  printf("\n  reference: equal 400.0, just 386.3, quarter-comma meantone 386.3\n");
  return 0;
}
