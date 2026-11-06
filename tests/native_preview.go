//go:build ignore

// Run from a Wox checkout's wox.core directory:
// go run /path/to/Mole/tests/native_preview.go /absolute/path/to/fixtures
// Uses the real native WoxView renderer with simulated cleanup data.
package main

import (
	"encoding/json"
	"fmt"
	"image"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"

	component "wox/ui/launcher/component"
	ui "wox/ui/runtime"
	"wox/ui/widget"
	"wox/ui/woxview"
)

type fixture struct {
	Name   string          `json:"name"`
	Dark   bool            `json:"dark"`
	Tree   json.RawMessage `json:"tree"`
	WaitMs int             `json:"waitMs"`
}

func color(r, g, b uint8) ui.Color { return ui.Color{R: r, G: g, B: b, A: 255} }

func main() {
	folder, err := filepath.Abs(os.Args[1])
	if err != nil {
		log.Fatal(err)
	}
	name := "scenes.json"
	if len(os.Args) > 2 {
		name = filepath.Base(os.Args[2]) + ".json"
	}
	data, err := os.ReadFile(filepath.Join(folder, name))
	if err != nil {
		log.Fatal(err)
	}
	var fixtures []fixture
	if err := json.Unmarshal(data, &fixtures); err != nil {
		log.Fatal(err)
	}
	trees := make([]*woxview.Tree, len(fixtures))
	for i, item := range fixtures {
		trees[i], err = woxview.Parse(item.Tree)
		if err != nil {
			log.Fatalf("%s: %v", item.Name, err)
		}
	}
	for _, name := range []string{"house.png", "house-base.png", "walls-atlas.png", "mole-atlas.png", "dust-puff.png"} {
		file, err := os.Open(filepath.Join(folder, "..", "..", "image", "scene", name))
		if err != nil {
			log.Fatal(err)
		}
		decoded, format, err := image.Decode(file)
		_ = file.Close()
		if err != nil {
			log.Fatal(err)
		}
		fmt.Println(name, format, decoded.Bounds())
		_, _, _, alpha := decoded.At(0, 0).RGBA()
		if alpha != 0 {
			log.Fatalf("%s: corner is not transparent", name)
		}
	}
	if err := ui.Run(func() error {
		view := woxview.NewView()
		view.SetTree(trees[0])
		current := 0
		var window *ui.Window
		host := widget.NewHost(func(frame ui.FrameInfo) widget.Widget {
			theme := component.ControlTheme{
				Background: color(250, 249, 246), Surface: color(242, 240, 235),
				Text: color(39, 38, 34), TextSecondary: color(100, 97, 92),
				Accent: color(154, 108, 64), Error: color(180, 35, 24),
				Border: color(225, 221, 214), DensityScale: 1,
			}
			if fixtures[current].Dark {
				theme.Dark = true
				theme.Background = color(28, 29, 32)
				theme.Surface = color(37, 39, 42)
				theme.Text = color(241, 241, 242)
				theme.TextSecondary = color(168, 173, 181)
				theme.Border = color(54, 56, 61)
				theme.Error = color(255, 145, 140)
			}
			width, height := frame.Size.Width, frame.Size.Height
			if strings.HasSuffix(fixtures[current].Name, "-compact") {
				width = 600
				height = 400
			}
			return widget.Container{Width: frame.Size.Width, Height: frame.Size.Height, Color: theme.Background,
				Child: view.Build(woxview.BuildProps{Width: width, Height: height, Theme: theme, Window: window})}
		})
		window, err = ui.Open(ui.WindowOptions{Title: "Mole native scene verification", Size: ui.Size{Width: 760, Height: 470},
			Role: ui.WindowRoleUtility, Nonactivating: true, Topmost: true, OnFrame: host.Frame})
		if err != nil {
			return err
		}
		host.Attach(window)
		if _, err := window.Show(); err != nil {
			return err
		}
		go func() {
			for i, item := range fixtures {
				_ = ui.Call(func() { current = i; view.SetTree(trees[i]); _ = window.Invalidate() })
				wait := item.WaitMs
				if wait <= 0 {
					wait = 600
				}
				time.Sleep(time.Duration(wait) * time.Millisecond)
				_ = ui.Call(func() {
					output := filepath.Join(folder, item.Name+".png")
					if err := window.CapturePNG(output); err != nil {
						log.Printf("capture: %v", err)
					} else {
						fmt.Println(output)
					}
				})
			}
			_ = ui.Call(func() { _ = window.Close() })
		}()
		return nil
	}); err != nil {
		log.Fatal(err)
	}
}
