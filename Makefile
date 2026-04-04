CC      = gcc
CFLAGS  = -O2 -Wall -Isrc -Ideps/raylib/include
LDFLAGS = -Ldeps/raylib/lib -lraylibdll -lopengl32 -lgdi32 -lwinmm
SRCS    = src/main.c src/noise.c src/world.c src/terrain.c src/input.c \
          src/sim/dirt.c src/sim/water.c src/sim/impact.c src/sim/blob.c \
          src/player.c src/rover.c src/rover_arm.c src/render.c
TARGET  = verdant_f1.exe

$(TARGET): $(SRCS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

clean:
	rm -f $(TARGET)
