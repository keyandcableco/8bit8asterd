/*
 * The bulk of this firmware comes from: https://dogemicrosystems.ca/wiki/Dual_AY-3-8910_MIDI_module
 * 
 * Firmware Version 2.2 for the 8-Bit 8asterd by Semiotic Sounds
 * 
 * Added:
 * Drum Polyphony
 * Slow attack evelopes on channels 3 and 4
 * Altered drum sounds (808 bass and clave)
 * Arduino Reset function on MIDI KillAll message
 * Better voice distribution methodology
 */

void(* resetFunc) (void) = 0;//declare reset function at address 0

// Uncomment to enable 5-pin DIN serial midi
#define SERIALMIDI

// Uncomment to enable USB midi
#define USBMIDI

// Uncomment to enable debugging output over USB serial
#define DEBUG

#include <avr/io.h>

#ifdef USBMIDI
#include "MIDIUSB.h"
#endif

// We borrow one struct from MIDIUSB for traditional serial midi, so define it if were not using USBMIDI
#ifndef MIDIUSB_h
typedef struct
{
  uint8_t header;
  uint8_t byte1;
  uint8_t byte2;
  uint8_t byte3;
} midiEventPacket_t;
#endif

#ifdef SERIALMIDI
#include <MIDI.h>
MIDI_CREATE_INSTANCE(HardwareSerial, Serial1, MIDI);//Serial1 on pro micro
#endif

typedef unsigned short int ushort;

typedef unsigned char note_t;
#define NOTE_OFF 0

typedef unsigned char midictrl_t;

// Pin driver ---------------------------------------------

static const int dbus[8] = { 2, 3, 4, 5, 6, 7, 8, 10 };

static const ushort
  BC2_A = 14,
  BDIR_A = 16,
  BC2_B = A1,
  BDIR_B = A0,
  BC2_C = A3,
  BDIR_C = A2,
  nRESET = 15,
  clkOUT = 9;

static const ushort DIVISOR = 7; // Set for 1MHz clock

static void clockSetup() {
   // Timer 1 setup for Mega32U4 devices
   //
   // Use CTC mode: WGM13..0 = 0100
   // Toggle OC1A on Compare Match: COM1A1..0 = 01
   // Use ClkIO with no prescaling: CS12..0 = 001
   // Interrupts off: TIMSK0 = 0
   // OCR0A = interval value
   
   TCCR1A = (1 << COM1A0);
   TCCR1B = (1 << WGM12) | (1 << CS10);
   TCCR1C = 0;
   TIMSK1 = 0;
   OCR1AH = 0;
   OCR1AL = DIVISOR; // NB write high byte first
}

static void setData(unsigned char db) {
  unsigned char bit = 1;
  for (ushort i = 0; i < 8; i++) {
    digitalWrite(dbus[i], (db & bit) ? HIGH : LOW);
    bit <<= 1;
  }
}

static void writeReg_A(unsigned char reg, unsigned char db) {
  // This is a bit of an odd way to do it, BC1 is kept low and NACT, BAR, IAB, and DWS are used.
  // BC1 is kept low the entire time.
  
  // Inactive (BDIR BC2 BC1 0 0 0)
  digitalWrite(BDIR_A, LOW);
  digitalWrite(BC2_A, LOW);

  //Set register address
  setData(reg);

  // BAR (Latch) (BDIR BC2 BC1 1 0 0)
  digitalWrite(BDIR_A, HIGH);

  // Inactive (BDIR BC2 BC1 0 0 0)
  digitalWrite(BDIR_A, LOW);
  
  //Set register contents
  setData(db);

  // Write (BDIR BC2 BC1 1 1 0)
  digitalWrite(BC2_A, HIGH);
  digitalWrite(BDIR_A, HIGH);

  // Inactive (BDIR BC2 BC1 0 0 0)
  digitalWrite(BC2_A, LOW);
  digitalWrite(BDIR_A, LOW);
}

static void writeReg_B(unsigned char reg, unsigned char db) {
  // This is a bit of an odd way to do it, BC1 is kept low and NACT, BAR, IAB, and DWS are used.
  // BC1 is kept low the entire time.
  
  // Inactive (BDIR BC2 BC1 0 0 0)
  digitalWrite(BDIR_B, LOW);
  digitalWrite(BC2_B, LOW);

  //Set register address
  setData(reg);

  // BAR (Latch) (BDIR BC2 BC1 1 0 0)
  digitalWrite(BDIR_B, HIGH);

  // Inactive (BDIR BC2 BC1 0 0 0)
  digitalWrite(BDIR_B, LOW);
  
  //Set register contents
  setData(db);

  // Write (BDIR BC2 BC1 1 1 0)
  digitalWrite(BC2_B, HIGH);
  digitalWrite(BDIR_B, HIGH);

  // Inactive (BDIR BC2 BC1 0 0 0)
  digitalWrite(BC2_B, LOW);
  digitalWrite(BDIR_B, LOW);
}

static void writeReg_C(unsigned char reg, unsigned char db) {
  // This is a bit of an odd way to do it, BC1 is kept low and NACT, BAR, IAB, and DWS are used.
  // BC1 is kept low the entire time.
  
  // Inactive (BDIR BC2 BC1 0 0 0)
  digitalWrite(BDIR_C, LOW);
  digitalWrite(BC2_C, LOW);

  //Set register address
  setData(reg);

  // BAR (Latch) (BDIR BC2 BC1 1 0 0)
  digitalWrite(BDIR_C, HIGH);

  // Inactive (BDIR BC2 BC1 0 0 0)
  digitalWrite(BDIR_C, LOW);
  
  //Set register contents
  setData(db);

  // Write (BDIR BC2 BC1 1 1 0)
  digitalWrite(BC2_C, HIGH);
  digitalWrite(BDIR_C, HIGH);

  // Inactive (BDIR BC2 BC1 0 0 0)
  digitalWrite(BC2_C, LOW);
  digitalWrite(BDIR_C, LOW);
}

// AY-3-8910 driver ---------------------------------------

class PSGRegs {
public:
  enum {
    TONEALOW = 0,
    TONEAHIGH,
    TONEBLOW,
    TONEBHIGH,
    TONECLOW,
    TONECHIGH,
    NOISEGEN,
    MIXER,
    TONEAAMPL,
    TONEBAMPL,
    TONECAMPL,
    ENVLOW,
    ENVHIGH,
    ENVSHAPE,
    IOA,
    IOB
  };

  //MIXER
//   0 = A+B+C+Noise            [0]
// 248 = A+B+C                  [1]
// 199 = Noise only on A+B+C    [2]
// 254 = A Only                 [3]
// 253 = B only                 [4]
// 251 = C only                 [5]
// 252 = A+B                    [6]
// 249 = B+C                    [7]
// 246 = A+Noise                [8]
// 237 = B+Noise                [9]
// 219 = C+Noise                [10]
// 238 = A+Noise on B           [11]
// 221 = B+Noise on C           [12]
// 243 = C+Noise on A           [13]
// 247 = Noise only on A        [14]
// 239 = Noise only on B        [15]
// 223 = Noise only on C        [16]
  
  unsigned char regs_A[16];
  unsigned char regs_B[16];
  unsigned char regs_C[16];

  unsigned char lastregs_A[16];
  unsigned char lastregs_B[16];
  unsigned char lastregs_C[16];

  void init() {
    for (int i = 0; i < 16; i++) {
      regs_A[i] = lastregs_A[i] = 0xFF;
      writeReg_A(i, regs_A[i]);

      regs_B[i] = lastregs_B[i] = 0xFF;
      writeReg_B(i, regs_B[i]);
      
      regs_C[i] = lastregs_C[i] = 0xFF;
      writeReg_C(i, regs_C[i]);
    }
  }



  
  void update_A() {
    for (int i = 0; i < 16; i++) {
      if (regs_A[i] != lastregs_A[i]) {
        writeReg_A(i, regs_A[i]);
        lastregs_A[i] = regs_A[i];
//        Serial.print("Reg A:");
//        Serial.print(regs_A[MIXER]);
//        Serial.print(", ");
//        Serial.print(regs_A[ENVSHAPE]);
//        Serial.print(", ");
//        Serial.println(regs_A[NOISEGEN]);
      }
    }
  }

  void update_B() {
    for (int i = 0; i < 16; i++) {
      if (regs_B[i] != lastregs_B[i]) {
        writeReg_B(i, regs_B[i]);
        lastregs_B[i] = regs_B[i];
//        Serial.print("Reg B: ");
//        Serial.print(regs_B[MIXER]);
//        Serial.print(", ");
//        Serial.print(regs_B[ENVSHAPE]);
//        Serial.print(", ");
//        Serial.println(regs_B[NOISEGEN]);        
      }
  }
 }

  void update_C() {
    for (int i = 0; i < 16; i++) {
      if (regs_C[i] != lastregs_C[i]) {
        writeReg_C(i, regs_C[i]);
        lastregs_C[i] = regs_C[i];
//        Serial.print("Reg C:");
//        Serial.print(regs_C[MIXER]);
//        Serial.print (", ");
//        Serial.print(regs_C[ENVSHAPE]);
//        Serial.print(", ");
//        Serial.println(regs_C[NOISEGEN]);        
      }      
    }
  }

  void update() {
    for (int i = 0; i < 16; i++) {
      if (regs_A[i] != lastregs_A[i]) {
        writeReg_A(i, regs_A[i]);
        lastregs_A[i] = regs_A[i];
      }

      if (regs_B[i] != lastregs_B[i]) {
        writeReg_B(i, regs_B[i]);
        lastregs_B[i] = regs_B[i];
      }
      
      if (regs_C[i] != lastregs_C[i]) {
        writeReg_C(i, regs_C[i]);
        lastregs_C[i] = regs_C[i];
      }      
    }
  }

  void setTone(ushort ch, ushort divisor, ushort ampl) {
    if (ch == 1 | ch == 4 | ch == 7) {
           if (ch==1){ch = 0;}
      else if (ch==4){ch = 1;}
      else if (ch==7){ch = 2;}
      regs_B[TONEALOW  + (ch<<1)] = (divisor & 0xFF);
      regs_B[TONEAHIGH + (ch<<1)] = (divisor >> 8);
      regs_B[TONEAAMPL + ch] = ampl;
      
      ushort mask = (8+1) << ch;
      regs_B[MIXER] = (regs_B[MIXER] | mask) ^ (1 << ch);

      return;
    }

   else if (ch == 2 | ch == 5 | ch == 8) {
           if (ch==2){ch = 0;}
      else if (ch==5){ch = 1;}
      else if (ch==8){ch = 2;}
      regs_C[TONEALOW  + (ch<<1)] = (divisor & 0xFF);
      regs_C[TONEAHIGH + (ch<<1)] = (divisor >> 8);
      regs_C[TONEAAMPL + ch] = ampl;
      
      ushort mask = (8+1) << ch;
      regs_C[MIXER] = (regs_C[MIXER] | mask) ^ (1 << ch);

      return;
    }

   else     if (ch == 0 | ch == 3 | ch == 6) {
           if (ch==0){ch = 0;}
      else if (ch==3){ch = 1;}
      else if (ch==6){ch = 2;}
    regs_A[TONEALOW  + (ch<<1)] = (divisor & 0xFF);
    regs_A[TONEAHIGH + (ch<<1)] = (divisor >> 8);
    regs_A[TONEAAMPL + ch] = ampl;
    
    ushort mask = (8+1) << ch;
    regs_A[MIXER] = (regs_A[MIXER] | mask) ^ (1 << ch);
    }
  }

  void setToneAndNoise(ushort ch, ushort divisor, ushort noisefreq, ushort ampl) {
    if (ch == 1 | ch == 4 | ch == 7) {
           if (ch==1){ch = 0;}
      else if (ch==4){ch = 1;}
      else if (ch==7){ch = 2;}
      regs_B[TONEALOW  + (ch<<1)] = (divisor & 0xFF);
      regs_B[TONEAHIGH + (ch<<1)] = (divisor >> 8);
      regs_B[NOISEGEN] = noisefreq;
      regs_B[TONEAAMPL + ch] = ampl;
      
      ushort mask = (8+1) << ch;
      ushort bits = (noisefreq < 16 ? 8 : 0) + (divisor > 0 ? 1 : 0);
      regs_B[MIXER] = (regs_B[MIXER] | mask) ^ (bits << ch);

      return;
    }


   else if (ch == 2 | ch == 5 | ch == 8) {
           if (ch==2){ch = 0;}
      else if (ch==5){ch = 1;}
      else if (ch==8){ch = 2;}
      regs_C[TONEALOW  + (ch<<1)] = (divisor & 0xFF);
      regs_C[TONEAHIGH + (ch<<1)] = (divisor >> 8);
      regs_C[NOISEGEN] = noisefreq;
      regs_C[TONEAAMPL + ch] = ampl;
      
      ushort mask = (8+1) << ch;
      ushort bits = (noisefreq < 16 ? 8 : 0) + (divisor > 0 ? 1 : 0);
      regs_C[MIXER] = (regs_C[MIXER] | mask) ^ (bits << ch);

      return;
    }

   else     if (ch == 0 | ch == 3 | ch == 6) {
           if (ch==0){ch = 0;}
      else if (ch==3){ch = 1;}
      else if (ch==6){ch = 2;}
      //Serial.println(ch);
    regs_A[TONEALOW  + (ch<<1)] = (divisor & 0xFF);
    regs_A[TONEAHIGH + (ch<<1)] = (divisor >> 8);
    regs_A[NOISEGEN] = noisefreq;
    regs_A[TONEAAMPL + ch] = ampl;
    
    ushort mask = (8+1) << ch;
    ushort bits = (noisefreq < 16 ? 8 : 0) + (divisor > 0 ? 1 : 0);
    regs_A[MIXER] = (regs_A[MIXER] | mask) ^ (bits << ch);
    }
  }

  void setEnvelope_A(ushort divisor, ushort shape) {
    regs_A[ENVLOW]  = (divisor & 0xFF);
    regs_A[ENVHIGH] = (divisor >> 8);
    regs_A[ENVSHAPE] = shape;
  }

    void setEnvelope_B(ushort divisor, ushort shape) {
    regs_B[ENVLOW]  = (divisor & 0xFF);
    regs_B[ENVHIGH] = (divisor >> 8);
    regs_B[ENVSHAPE] = shape; 
  }


  void setEnvelope_C(ushort divisor, ushort shape) {
    regs_C[ENVLOW]  = (divisor & 0xFF);
    regs_C[ENVHIGH] = (divisor >> 8);
    regs_C[ENVSHAPE] = shape; 
  }


    void setPercOff(ushort ch) {
    if (ch == 1 | ch == 4 | ch == 7) {
           if (ch==1){ch = 0;}
      else if (ch==4){ch = 1;}
      else if (ch==7){ch = 2;}

      ushort mask = (8+1) << ch;
      regs_B[MIXER] = (regs_B[MIXER] | mask);
      regs_B[TONEAAMPL + ch] = 0;
      if (regs_B[ENVSHAPE] != 0) {
        regs_B[ENVSHAPE] = 0;
        update_B(); // Force flush
      }

      return;
    }

 else if (ch == 2 | ch == 5 | ch == 8) {
           if (ch==2){ch = 0;}
      else if (ch==5){ch = 1;}
      else if (ch==8){ch = 2;}

      ushort mask = (8+1) << ch;
      regs_C[MIXER] = (regs_A[MIXER] | mask);
      regs_C[TONEAAMPL + ch] = 0;
      if (regs_C[ENVSHAPE] != 0) {
        regs_C[ENVSHAPE] = 0;
        update_C(); // Force flush
      }

      return;
    }
   else  if (ch == 0 | ch == 3 | ch == 6) {
           if (ch==0){ch = 0;}
      else if (ch==3){ch = 1;}
      else if (ch==6){ch = 2;}
      //Serial.println("Chip A off");
    ushort mask = (8+1) << ch;
    regs_A[MIXER] = (regs_C[MIXER] | mask);
    regs_A[TONEAAMPL + ch] = 0;
    if (regs_A[ENVSHAPE] != 0) {
      regs_A[ENVSHAPE] = 0;
      update_A(); // Force flush
    }
    }
  }
//  
 void setOff(ushort ch) {
        if (ch == 1 | ch == 4 | ch == 7) {
           if (ch==1){ch = 0;}
      else if (ch==4){ch = 1;}
      else if (ch==7){ch = 2;}
      

      ushort mask = (8+1) << ch;
      regs_B[MIXER] = (regs_B[MIXER] | mask);
      regs_B[TONEAAMPL + ch] = 0;
      if (regs_B[ENVSHAPE] != 0) {
        regs_B[ENVSHAPE] = 0;
        update_B(); // Force flush
      }

      return;
    }

 else if (ch == 2 | ch == 5 | ch == 8) {
           if (ch==2){ch = 0;}
      else if (ch==5){ch = 1;}
      else if (ch==8){ch = 2;}

      ushort mask = (8+1) << ch;
      regs_C[MIXER] = (regs_C[MIXER] | mask);
      regs_C[TONEAAMPL + ch] = 0;
      if (regs_C[ENVSHAPE] != 0) {
        regs_C[ENVSHAPE] = 0;
        update_C(); // Force flush
      }

      return;
    }
   else  if (ch == 0 | ch == 3 | ch == 6) {
           if (ch==0){ch = 0;}
      else if (ch==3){ch = 1;}
      else if (ch==6){ch = 2;}

    ushort mask = (8+1) << ch;
    regs_A[MIXER] = (regs_A[MIXER] | mask);
    regs_A[TONEAAMPL + ch] = 0;
    if (regs_A[ENVSHAPE] != 0) {
      regs_A[ENVSHAPE] = 0;
      update_A(); // Force flush
    }
    }
  }
};

static PSGRegs psg;

// Voice generation ---------------------------------------

static const ushort
  MIDI_MIN = 24,
  MIDI_MAX = 96,
  N_NOTES = (MIDI_MAX+1-MIDI_MIN);

static const ushort note_table[N_NOTES] = {
   1911, // MIDI 24, 32.70 Hz
   1804, // MIDI 25, 34.65 Hz
   1703, // MIDI 26, 36.71 Hz
   1607, // MIDI 27, 38.89 Hz
   1517, // MIDI 28, 41.20 Hz
   1432, // MIDI 29, 43.65 Hz
   1351, // MIDI 30, 46.25 Hz
   1276, // MIDI 31, 49.00 Hz
   1204, // MIDI 32, 51.91 Hz
   1136, // MIDI 33, 55.00 Hz
   1073, // MIDI 34, 58.27 Hz
   1012, // MIDI 35, 61.74 Hz
   956, // MIDI 36, 65.41 Hz
   902, // MIDI 37, 69.30 Hz
   851, // MIDI 38, 73.42 Hz
   804, // MIDI 39, 77.78 Hz
   758, // MIDI 40, 82.41 Hz
   716, // MIDI 41, 87.31 Hz
   676, // MIDI 42, 92.50 Hz
   638, // MIDI 43, 98.00 Hz
   602, // MIDI 44, 103.83 Hz
   568, // MIDI 45, 110.00 Hz
   536, // MIDI 46, 116.54 Hz
   506, // MIDI 47, 123.47 Hz
   478, // MIDI 48, 130.81 Hz
   451, // MIDI 49, 138.59 Hz
   426, // MIDI 50, 146.83 Hz
   402, // MIDI 51, 155.56 Hz
   379, // MIDI 52, 164.81 Hz
   358, // MIDI 53, 174.61 Hz
   338, // MIDI 54, 185.00 Hz
   319, // MIDI 55, 196.00 Hz
   301, // MIDI 56, 207.65 Hz
   284, // MIDI 57, 220.00 Hz
   268, // MIDI 58, 233.08 Hz
   253, // MIDI 59, 246.94 Hz
   239, // MIDI 60, 261.63 Hz
   225, // MIDI 61, 277.18 Hz
   213, // MIDI 62, 293.66 Hz
   201, // MIDI 63, 311.13 Hz
   190, // MIDI 64, 329.63 Hz
   179, // MIDI 65, 349.23 Hz
   169, // MIDI 66, 369.99 Hz
   159, // MIDI 67, 392.00 Hz
   150, // MIDI 68, 415.30 Hz
   142, // MIDI 69, 440.00 Hz
   134, // MIDI 70, 466.16 Hz
   127, // MIDI 71, 493.88 Hz
   119, // MIDI 72, 523.25 Hz
   113, // MIDI 73, 554.37 Hz
   106, // MIDI 74, 587.33 Hz
   100, // MIDI 75, 622.25 Hz
   95, // MIDI 76, 659.26 Hz
   89, // MIDI 77, 698.46 Hz
   84, // MIDI 78, 739.99 Hz
   80, // MIDI 79, 783.99 Hz
   75, // MIDI 80, 830.61 Hz
   71, // MIDI 81, 880.00 Hz
   67, // MIDI 82, 932.33 Hz
   63, // MIDI 83, 987.77 Hz
   60, // MIDI 84, 1046.50 Hz
   56, // MIDI 85, 1108.73 Hz
   53, // MIDI 86, 1174.66 Hz
   50, // MIDI 87, 1244.51 Hz
   47, // MIDI 88, 1318.51 Hz
   45, // MIDI 89, 1396.91 Hz
   42, // MIDI 90, 1479.98 Hz
   40, // MIDI 91, 1567.98 Hz
   38, // MIDI 92, 1661.22 Hz
   36, // MIDI 93, 1760.00 Hz
   34, // MIDI 94, 1864.66 Hz
   32, // MIDI 95, 1975.53 Hz
   30, // MIDI 96, 2093.00 Hz
};

struct FXParams {
  ushort noisefreq;
  ushort tonefreq;
  ushort envdecay;
  ushort freqdecay;
  ushort timer;
  ushort shape;
};

struct ToneParams {
  ushort attack;
  ushort decay;
  ushort sustain; // Values 0..32
  ushort rel;
};

static const ushort MAX_TONES = 4;
static const ToneParams tones[MAX_TONES] = {
  { 1, 8, 32, 32 },
  { 1 , 8,  1,  32 },
  { 8, 1, 1, 32 },
  { 4, 1, 32, 1 }
};

class Voice {
public:
  ushort m_chan;  // Index to psg channel 
  ushort m_pitch;
  int m_attack, m_ampl, m_ampl_top, m_decay, m_sustain, m_release, m_shape;
  static const int AMPL_MAX = 1023;
  ushort m_adsr;
  ushort m_vel;

  void init (ushort chan) {
    m_chan = chan;
    m_ampl = m_sustain = 0;
    kill();
  }
  
  void start(note_t note, midictrl_t vel, midictrl_t chan) {
    const ToneParams *tp = &tones[chan % MAX_TONES];
    
    m_pitch = note_table[note - MIDI_MIN];
    m_vel = 768 + (vel << 1);
    m_attack = tp->attack;
    m_decay = tp->decay; 
    //m_decay = 0; 

        if (vel > 127) {
      m_ampl = AMPL_MAX;
      m_ampl_top = AMPL_MAX;
    }

    
     if (m_attack > 1) {
      m_adsr = 'A';
      //m_ampl = 768 + (vel << 1);
      m_ampl = 255 + (m_attack >> 1);
    }



    else {
      m_ampl = 768 + (vel << 1);
      m_adsr = 'D';
      }
    
    m_sustain = (m_ampl * tp->sustain) >> 5;
    
    m_release = tp->rel;
    
    
    //psg.setEnvelope(32, 11); 
    psg.setTone(m_chan, m_pitch, m_ampl >> 6);
  }

  struct FXParams m_fxp;
  
  void startFX(const struct FXParams &fxp) {
    m_fxp = fxp;
  
    if (m_ampl > 0) {
      psg.setPercOff(m_chan);
    }
    m_ampl = AMPL_MAX;
    //m_ampl = 255 + (m_vel << 1);
    m_adsr = 'X';
    m_decay = fxp.timer;
    m_shape = fxp.shape;
    //m_decay = 96;


// 9 = Just release             [0]
// 4 = Reverse Release          [1]
// 8 = Repeat Release           [2]
// 12 = Reverse Repeat Release  [3]
// 10 = Repeat Release Waves    [4]
// 14 = Reverse Repeat Waves    [5]
// 11 = Release and on          [6]


  if (m_chan == 1 || m_chan == 4 || m_chan == 7){
    psg.setEnvelope_B(fxp.envdecay, m_shape);
  }  
  else if (m_chan == 2 || m_chan == 5 || m_chan == 8){
    psg.setEnvelope_C(fxp.envdecay, m_shape);
  }
  else if (m_chan == 0 || m_chan == 3 || m_chan == 6){
    psg.setEnvelope_A(fxp.envdecay, m_shape);
  }
  Serial.print("CH: ");
  Serial.println(m_chan);
    psg.setToneAndNoise(m_chan, fxp.tonefreq, fxp.noisefreq, 30);
  }
  

  void stop() {
    if (m_adsr == 'X') {
      //psg.setPercOff(m_chan);
      return; // Will finish when ready...
    }
      
    if (m_ampl > 0) {
      m_adsr = 'R';
    }
    
    else {
      psg.setOff(m_chan);
      //psg.setPercOff(m_chan);
    }
  }
  
  void update100Hz() {
    //m_attack = m_attack*1;
    
   if (m_ampl == 0) {
      return;
    }
    
    switch(m_adsr) {
//
      case 'A':
        if (m_ampl < m_vel) {
         
        m_ampl += (m_attack << 1);
        //m_adsr = 'A';
        }
//
        
        
        if (m_ampl >= m_vel) {
          m_adsr = 'D';
        };
       
        
        break;
        
      case 'D':
        
        if (m_ampl <= m_sustain) {
          m_adsr = 'S';
          m_ampl = m_sustain;
        }
        
        m_ampl -= m_decay;
        
        break;

      case 'S':
        break;

      case 'R':
        if ( m_ampl < m_release ) {
          m_ampl = 0;
        }
        else {
          m_ampl -= m_release;
        }
        break;

      case 'X':
        // FX is playing.         
        if (m_fxp.freqdecay > 0) { 
          m_fxp.tonefreq += m_fxp.freqdecay;
          //psg.setEnvelope(m_fxp.envdecay, 9); 
          psg.setToneAndNoise(m_chan, m_fxp.tonefreq, m_fxp.noisefreq, 30);
        }
        
        m_ampl -= m_decay;
        
        if (m_ampl < 0) {
   
          //psg.setOff(m_chan);
          psg.setPercOff(m_chan); 
          m_ampl = 0;
        }
        return;
        
      default:
        break;
    }  

    if (m_ampl > 0) {          
      psg.setTone(m_chan, m_pitch, m_ampl >> 6);
    }
    else {
      psg.setOff(m_chan);    
      psg.setPercOff(m_chan);  
    }
  }
  
  bool isPlaying() {
    return (m_ampl > 0);
  }
  
  void kill() {
    psg.setOff(m_chan);
    psg.setPercOff(m_chan);
    m_ampl = 0;
  }
};


const ushort MAX_VOICES = 9;

static Voice voices[MAX_VOICES];

// MIDI synthesiser ---------------------------------------

// Deals with assigning note on/note off to voices

static const uint8_t PERC_CHANNEL = 9;

static const note_t
  PERC_MIN = 35,
  PERC_MAX = 50;
  
static const struct FXParams perc_params[PERC_MAX-PERC_MIN+1] = {
  // Mappings are from the General MIDI spec at https://www.midi.org/specifications-old/item/gm-level-1-sound-set
  
  // Params are: noisefreq, tonefreq, envdecay, freqdecay, timer, shape
  
  { 9, 900, 800, 40, 50, 9 },   // 35 Acoustic bass drum
  { 16, 2000, 1500, 16, 15, 9 },  // 36 (C) 808 Bass Drum 1
  { 5, 0, 300, 0, 80, 9 },      // 37 Side Stick
  { 6, 0, 1200, 0, 30, 9  },    // 38 Acoustic snare
  
  { 16, 75, 150, 0, 60, 9 },     // 39 (D#) Clave
  { 6, 400, 1200, 11, 30, 9  }, // 40 Electric snare
  { 16, 700, 800, 20, 30, 9 },  // 41 Low floor tom
  { 0, 0, 300, 0, 80, 9 },      // 42 Closed Hi Hat
  
  { 16, 400, 800, 13, 30, 9 },   // 43 (G) High Floor Tom
  { 0, 0, 600, 0, 50, 9 },      // 44 Pedal Hi-Hat
  { 16, 800, 1400, 30, 25, 9 }, // 45 Low Tom
  { 0, 0, 800, 0, 40, 9 },      // 46 Open Hi-Hat
  
  { 16, 600, 1400, 20, 25, 9 }, // 47 (B) Low-Mid Tom
  { 16, 450, 1500, 15, 22, 9 }, // 48 Hi-Mid Tom
  { 16, 320, 1500, 20, 22, 9 },     // 49 Effect #1
  { 16, 300, 1500, 10, 22, 9 }, // 50 High Tom
};
  
  

static const int REQ_MAP_SIZE = (N_NOTES+7) / 8;
static uint8_t m_requestMap[REQ_MAP_SIZE];
  // Bit is set for each note being requested
static  midictrl_t m_velocity[N_NOTES];
  // Requested velocity for each note
static  midictrl_t m_chan[N_NOTES];
  // Requested MIDI channel for each note
static uint8_t m_highest, m_lowest;
  // Highest and lowest requested notes

static const uint8_t NO_NOTE = 0xFF;
static const uint8_t PERC_NOTE = 0xFE;
static uint8_t m_playing[MAX_VOICES];
  // Which note each voice is playing

static const uint8_t NO_VOICE = 0xFF;
static uint8_t m_voiceNo[N_NOTES];
  // Which voice is playing each note
  

static bool startNote(ushort idx) {
  for (ushort i = 0; i < MAX_VOICES; i++) {
    if (m_playing[i] == NO_NOTE) {
      voices[i].start(MIDI_MIN + idx, m_velocity[idx], m_chan[idx]);
      m_playing[i] = idx;
      m_voiceNo[idx] = i;
          //Serial.print("Voice #: ");
          //Serial.println(i);
      return true;
    }
  }
  return false;
}
  
static bool startPercussion(note_t note) {
  ushort i;
  for (i = 0; i < MAX_VOICES; i++) {
    //if (m_playing[i] == NO_NOTE || m_playing[i] == PERC_NOTE) {
    if (i != NO_VOICE && m_playing[i] == NO_NOTE) {
      if (note >= PERC_MIN && note <= PERC_MAX) {
        voices[i].startFX(perc_params[note-PERC_MIN]);
        m_playing[i] = PERC_NOTE;

      }
      return true;
    }        
//              Serial.print("m_playing: ");
//          Serial.println(m_playing[i]);
  }
  return false;
}
    
static bool stopNote(ushort idx) {
  uint8_t v = m_voiceNo[idx];
  if (v != NO_VOICE && m_playing[v] != NO_NOTE) {
    voices[v].stop();
    m_playing[v] = NO_NOTE;
    m_voiceNo[idx] = NO_VOICE;
    return true;
  }
  return false;
}

static void stopOneNote() {
  uint8_t v, chosen = NO_NOTE;

  // At this point we have run out of voices.
  // Pick a voice and stop it. We leave a voice alone
  // if it's playing the highest requested note. If it's
  // playing the lowest requested note we look for a 'better'
  // note, but stop it if none found.

  for (v = 0; v < MAX_VOICES; v++) {
    uint8_t idx = m_playing[v];
    if (idx == NO_NOTE) {// Uh? Perhaps called by mistake.
      return;
    }

    if (idx == m_highest) {
      continue;
    }

    if (idx == PERC_NOTE) {
      continue;
    }
      
    chosen = idx;
    if (idx != m_lowest) {
      break;
    }
    // else keep going, we may find a better one
  }

  if (chosen != NO_NOTE) {
    stopNote(chosen);
  }
}

static void updateRequestedNotes() {
  m_highest = m_lowest = NO_NOTE;
  ushort i,j;
    
  // Check highest requested note is playing
  // Return true if note was restarted; false if already playing 
  for (i = 0; i < REQ_MAP_SIZE; i++) {
    uint8_t req = m_requestMap[i];
    if (req == 0) {
      continue;
    }

    for (j = 0; j < 8; j++) {
      if (req & (1 << j)) {
        ushort idx = i*8 + j;
        if (m_lowest == NO_NOTE || m_lowest > idx) {
          m_lowest = idx;
        }
        if (m_highest==NO_NOTE || m_highest < idx)  {
          m_highest = idx;
        }
      }
    }
  }
}

static bool restartANote() {
  if (m_highest != NO_NOTE && m_voiceNo[m_highest] == NO_VOICE) {
    return startNote(m_highest);
  }

  if (m_lowest != NO_NOTE && m_voiceNo[m_lowest] == NO_VOICE) {
    return startNote(m_lowest);
  }

  return false;
}

static void synth_init () {
  ushort i;

  for (i = 0; i < REQ_MAP_SIZE; i++) {
    m_requestMap[i] = 0;
  }

  for (i = 0; i < N_NOTES; i++) {
    m_velocity[i] = 0;
    m_voiceNo[i] = NO_VOICE;
  }
    
  for (i = 0; i < MAX_VOICES; i++) {
    m_playing[i] = NO_NOTE;
  }
    
  m_highest = m_lowest = NO_NOTE;
}

static void noteOff(midictrl_t chan, note_t note, midictrl_t vel) {
  if (chan == PERC_CHANNEL || note < MIDI_MIN || note > MIDI_MAX) {
    return; // Just ignore it
  }

    if (note < MIDI_MIN || note > MIDI_MAX) {
    return; // Just ignore it
  }

  ushort idx = note - MIDI_MIN;

  m_requestMap[idx/8] &= ~(1 << (idx & 7));
  m_velocity[idx] = 0;
  updateRequestedNotes();
    
  if (stopNote(idx)) {
    restartANote();
  }
}

static void noteOn(midictrl_t chan, note_t note, midictrl_t vel) {
  if (vel == 0) {
    noteOff(chan, note, 0);
    return;
  }

  if (chan == PERC_CHANNEL) {
    //if (!startPercussion(note)) {
      stopOneNote();
      startPercussion(note);
    //}
    return;
  }
    
  // Regular note processing now
    
  if (note < MIDI_MIN || note > MIDI_MAX) {
    return; // Just ignore it
  }

  ushort idx = note - MIDI_MIN;
    
  if (m_voiceNo[idx] != NO_VOICE) {
    return; // Already playing. Ignore this request.
  }

  m_requestMap[idx/8] |= 1 << (idx & 7);
  m_velocity[idx] = vel;
  m_chan[idx] = chan;
  updateRequestedNotes();
    
  if (!startNote(idx)) {
     stopOneNote();
     startNote(idx);
  }
}
  
  
static void update100Hz() {
  for (ushort i = 0; i < MAX_VOICES; i++) {
    voices[i].update100Hz();
    if (m_playing[i] == PERC_NOTE && ! (voices[i].isPlaying())) {
    //if (! (voices[i].isPlaying())) {      
      //for (ushort j = 0; j < 2; j++) {
      m_playing[i] = NO_NOTE;
      restartANote();
      //}
    }        
  }
}



// Main code ----------------------------------------------

static unsigned long lastUpdate = 0;

void setup() {
  // Hold in reset while we set up the reset
  pinMode(nRESET, OUTPUT);
  digitalWrite(nRESET, LOW);

  pinMode(clkOUT, OUTPUT);
  digitalWrite(clkOUT, LOW);
  clockSetup();

  pinMode(BC2_A, OUTPUT);
  digitalWrite(BC2_A, LOW); // BC2 low
  pinMode(BDIR_A, OUTPUT);
  digitalWrite(BDIR_A, LOW); // BDIR low

  pinMode(BC2_B, OUTPUT);
  digitalWrite(BC2_B, LOW); // BC2 low
  pinMode(BDIR_B, OUTPUT);
  digitalWrite(BDIR_B, LOW); // BDIR low

  pinMode(BC2_C, OUTPUT);
  digitalWrite(BC2_C, LOW); // BC2 low
  pinMode(BDIR_C, OUTPUT);
  digitalWrite(BDIR_C, LOW); // BDIR low

  for (ushort i = 0; i < 8; i++) {
    pinMode(dbus[i], OUTPUT);
    digitalWrite(dbus[i], LOW); // Set bus low
  }

  delay(100);
  digitalWrite(nRESET, HIGH); // Release Reset
  delay(10);

  lastUpdate = millis();
  
  psg.init();
  for (ushort i = 0; i < MAX_VOICES; i++) {
    voices[i].init(i);
  }
  synth_init();

#ifdef DEBUG
    Serial.begin(115200);
#endif

#ifdef SERIALMIDI
  // Initiate MIDI communications, listen to all channels
  MIDI.begin(MIDI_CHANNEL_OMNI);
#endif
}

void handleMidiMessage(midiEventPacket_t &rx) {
  if (rx.header==0x9) {// Note on
    noteOn(rx.byte1 & 0xF, rx.byte2, rx.byte3);
  }
  else if (rx.header==0x8) {// Note off
    noteOff(rx.byte1 & 0xF, rx.byte2, rx.byte3);
  }
  else if (rx.header==0xB) {// Control Change
    if (rx.byte2 == 0x78 || rx.byte2 == 0x79 || rx.byte2 == 0x7B) {// AllSoundOff, ResetAllControllers, or AllNotesOff
      // Kill Voices
      //for (ushort i = 0; i < MAX_VOICES; i++) {
        //voices[i].kill();
        //delay(1);
        //psg.init();
        //delay(1);
        resetFunc();
      //}
    }
  }
}

void loop() {
  midiEventPacket_t rx;

#ifdef USBMIDI
  rx = MidiUSB.read();

#ifdef DEBUG
  //MIDI debugging
  if (rx.header != 0) {
    Serial.print("Received USB: ");
    Serial.print(rx.header, HEX);
    Serial.print("-");
    Serial.print(rx.byte1, HEX);
    Serial.print("-");
    Serial.print(rx.byte2, HEX);
    Serial.print("-");
    Serial.println(rx.byte3, HEX);
  }
#endif

  handleMidiMessage(rx);
#endif

#ifdef SERIALMIDI
  //Check for serial MIDI messages
  //MIDI.read();
  while (MIDI.read()) {
    // Create midiEventPacket_t
    rx = 
      {
        byte(MIDI.getType() >>4), 
        byte(MIDI.getType() | ((MIDI.getChannel()-1) & 0x0f)), /* getChannel() returns values from 1 to 16 */
        MIDI.getData1(), 
        MIDI.getData2()
      };

#ifdef DEBUG
    //MIDI debugging
    if (rx.header != 0) {
      Serial.print("Received MIDI: ");
      Serial.print(rx.header, HEX);
      Serial.print("-");
      Serial.print(rx.byte1, HEX);
      Serial.print("-");
      Serial.print(rx.byte2, HEX);
      Serial.print("-");
      Serial.println(rx.byte3, HEX);
    }
#endif

    handleMidiMessage(rx);
  }
#endif

  unsigned long now = millis();
  if ((now - lastUpdate) > 10) {
    update100Hz();
    lastUpdate += 5;
  }
  
  psg.update_A();
  //delay(1);
  psg.update_B();
  //delay(1);
  psg.update_C();
  //delay(1);
//  psg.update();

}
