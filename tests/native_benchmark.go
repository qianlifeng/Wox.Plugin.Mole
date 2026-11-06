//go:build ignore

// Run from wox.core with a fixture directory and optional duration in seconds.
// This renders simulated cleanup continuously without updates, captures or real cleanup.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"time"

	component "wox/ui/launcher/component"
	ui "wox/ui/runtime"
	"wox/ui/widget"
	"wox/ui/woxview"
)

// main records actual native uploads and frame work while one immutable scene plays.
func main() {
	folder := os.Args[1]
	seconds := 120
	if len(os.Args) > 2 {
		seconds, _ = strconv.Atoi(os.Args[2])
	}
	data, err := os.ReadFile(filepath.Join(folder, "scenes.json"))
	if err != nil {
		panic(err)
	}
	var fixtures []struct {
		Name string          `json:"name"`
		Tree json.RawMessage `json:"tree"`
	}
	if err = json.Unmarshal(data, &fixtures); err != nil {
		panic(err)
	}
	var tree *woxview.Tree
	for _, f := range fixtures {
		if f.Name == "sweeping-light" {
			tree, err = woxview.Parse(f.Tree)
		}
	}
	if err != nil || tree == nil {
		panic(fmt.Sprint("fixture: ", err))
	}
	var report map[string]any
	err = ui.Run(func() error {
		view := woxview.NewView()
		view.SetTree(tree)
		var window *ui.Window
		host := widget.NewHost(func(frame ui.FrameInfo) widget.Widget {
			theme := component.ControlTheme{Background: ui.Color{R: 248, G: 245, B: 233, A: 255}, Text: ui.Color{R: 30, G: 30, B: 28, A: 255}, TextSecondary: ui.Color{R: 110, G: 110, B: 108, A: 255}, DensityScale: 1}
			return widget.Container{Width: frame.Size.Width, Height: frame.Size.Height, Color: theme.Background, Child: view.Build(woxview.BuildProps{Width: frame.Size.Width, Height: frame.Size.Height, Theme: theme, Window: window})}
		})
		window, err = ui.Open(ui.WindowOptions{Title: "Mole animation performance check", Size: ui.Size{Width: 760, Height: 470}, Nonactivating: true, Role: ui.WindowRoleUtility, OnFrame: host.Frame})
		if err != nil {
			return err
		}
		host.Attach(window)
		if _, err = window.ShowInactive(); err != nil {
			return err
		}
		go func() {
			started := time.Now()
			lastFrame := uint64(0)
			var uploads, hits, evictions int
			var frameTimes []int64
			var memory []map[string]uint64
			var resident int64
			for second := 1; second <= seconds; second++ {
				time.Sleep(time.Second)
				metrics := window.FrameMetrics()
				for _, frame := range metrics.Recent {
					if frame.FrameID <= lastFrame {
						continue
					}
					lastFrame = frame.FrameID
					resident = frame.RendererResources.ResidentBytes
					if second > 5 {
						uploads += frame.RendererResources.ImageUploads
						hits += frame.RendererResources.CacheHits
						evictions += frame.RendererResources.CacheEvictions
						frameTimes = append(frameTimes, frame.BuildLayoutMicroseconds+frame.DrawRecordMicroseconds+frame.NativeEncodeMicroseconds)
					}
				}
				if second%15 == 0 || second == seconds {
					var m runtime.MemStats
					runtime.ReadMemStats(&m)
					point := map[string]uint64{"second": uint64(second), "heapAlloc": m.HeapAlloc, "heapInuse": m.HeapInuse, "totalAlloc": m.TotalAlloc, "gc": uint64(m.NumGC), "rendererResident": uint64(resident)}
					memory = append(memory, point)
					encoded, _ := json.Marshal(point)
					fmt.Println(string(encoded))
				}
			}
			sort.Slice(frameTimes, func(i, j int) bool { return frameTimes[i] < frameTimes[j] })
			percentile := func(p float64) int64 {
				if len(frameTimes) == 0 {
					return 0
				}
				return frameTimes[min(len(frameTimes)-1, int(float64(len(frameTimes))*p))]
			}
			report = map[string]any{"seconds": time.Since(started).Seconds(), "treeUpdatesAfterMount": 0, "measuredFrames": len(frameTimes), "warmImageUploads": uploads, "warmCacheHits": hits, "warmEvictions": evictions, "frameWorkP50us": percentile(.5), "frameWorkP95us": percentile(.95), "frameWorkP99us": percentile(.99), "memory": memory}
			_ = window.Hide()
			time.Sleep(time.Second)
			before := window.FrameMetrics().FrameCount
			time.Sleep(2 * time.Second)
			report["hiddenFrames"] = window.FrameMetrics().FrameCount - before
			_ = ui.Call(func() { host.Dispose(); view.SetTree(nil); _ = window.Close() })
		}()
		return nil
	})
	if err != nil {
		panic(err)
	}
	encoded, _ := json.MarshalIndent(report, "", "  ")
	if err = os.WriteFile(filepath.Join(folder, "benchmark.json"), encoded, 0644); err != nil {
		panic(err)
	}
	fmt.Println(string(encoded))
}
