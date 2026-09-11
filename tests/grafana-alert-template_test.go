package alerting_test

import (
	"bytes"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"text/template"
	"time"
)

type alert struct {
	Status                                  string
	Labels, Annotations                     map[string]string
	StartsAt, EndsAt                        time.Time
	GeneratorURL, DashboardURL, ValueString string
}
type alerts []alert

func (a alerts) Firing() (out alerts) {
	for _, v := range a {
		if v.Status == "firing" {
			out = append(out, v)
		}
	}
	return
}
func (a alerts) Resolved() (out alerts) {
	for _, v := range a {
		if v.Status == "resolved" {
			out = append(out, v)
		}
	}
	return
}

func TestDiscordMessage(t *testing.T) {
	source, err := exec.Command("yq", "-r", ".templates[0].template", "../platform/monitoring/files/alerting/templates.yaml").Output()
	if err != nil {
		t.Fatal(err)
	}
	tmpl, err := template.New("discord").Parse(string(source))
	if err != nil {
		t.Fatal(err)
	}
	start := time.Date(2026, 9, 7, 12, 0, 0, 0, time.UTC)
	firing := alert{Status: "firing", Labels: map[string]string{"host": "kepler", "cluster": "homelab", "severity": "warning", "source_ip": "SECRET_IP", "ssh_user": "SECRET_USER", "fingerprint": "SECRET_FP"}, Annotations: map[string]string{"summary": "Backup age 10d exceeds 9d; pending 1h", "error": "SECRET_ERROR"}, StartsAt: start, GeneratorURL: "https://grafana.example/alerting/grafana/backup/view", ValueString: "SECRET_VALUE"}
	resolved := firing
	resolved.Status, resolved.EndsAt = "resolved", start.Add(time.Hour)
	title, runbook := "Backup stale", "https://example.com/runbook"
	render := func(name string, list alerts) string {
		t.Helper()
		var out bytes.Buffer
		data := struct {
			Alerts                          alerts
			CommonLabels, CommonAnnotations map[string]string
		}{list, map[string]string{"alertname": title}, map[string]string{"runbook_url": runbook}}
		if err := tmpl.ExecuteTemplate(&out, name, data); err != nil {
			t.Fatal(err)
		}
		return out.String()
	}
	message := render("homelab.discord.message", alerts{firing, resolved})
	for _, want := range []string{"kepler", "homelab", "warning", "2026-09-07 12:00 UTC", "2026-09-07 13:00 UTC", "Previous condition:", "https://example.com/runbook", "docs/alerting.md", "Backup age 10d"} {
		if !strings.Contains(message, want) {
			t.Errorf("message missing %q", want)
		}
	}
	for _, forbidden := range []string{"SECRET_", "<no value>", "0001-01-01"} {
		if strings.Contains(message, forbidden) {
			t.Errorf("message contains %q", forbidden)
		}
	}
	if title := render("homelab.discord.title", alerts{firing, resolved}); !strings.Contains(title, "FIRING (1)") || !strings.Contains(title, "RESOLVED (1)") {
		t.Errorf("mixed title: %s", title)
	}
	if title := render("homelab.discord.title", alerts{resolved}); !strings.Contains(title, "RESOLVED (1)") {
		t.Errorf("resolved title: %s", title)
	}
	resolved.Labels = map[string]string{"instance": "endeavour"}
	if message := render("homelab.discord.message", alerts{resolved}); !strings.Contains(message, "endeavour") || strings.Contains(message, "🔴") {
		t.Errorf("resolved instance fallback: %s", message)
	}
	if message := render("homelab.discord.message", nil); strings.Contains(message, "<no value>") {
		t.Errorf("empty group: %s", message)
	}
	missing := firing
	missing.Labels, missing.Annotations = map[string]string{"datasource_uid": "prometheus"}, map[string]string{"error": "SECRET_ERROR"}
	message = render("homelab.discord.message", alerts{missing})
	if !strings.Contains(message, "prometheus") || !strings.Contains(message, "Summary unavailable") || strings.Contains(message, "SECRET_") {
		t.Errorf("unsafe or empty missing-summary fallback: %s", message)
	}
	var many alerts
	for i := 0; i < 20; i++ {
		a := firing
		a.Annotations = map[string]string{"summary": strings.Repeat("x", 1000)}
		a.Labels = map[string]string{"host": strings.Repeat("h", 100), "cluster": strings.Repeat("c", 100), "datasource_uid": strings.Repeat("d", 100), "severity": "critical"}
		many = append(many, a)
		a.Status, a.EndsAt = "resolved", start.Add(time.Hour)
		many = append(many, a)
	}
	message = render("homelab.discord.message", many)
	if len(message) > 4096 || !strings.Contains(message, "omitted") || !strings.Contains(message, "[Rule]") {
		t.Errorf("unbounded or undiscoverable truncation: %d bytes", len(message))
	}
	// Titles and runbook URLs are curated provisioning, not arbitrary labels.
	// Test their actual source maxima with the fully populated oversized group.
	files, err := filepath.Glob("../platform/monitoring/files/alerting/*rules.yaml")
	if err != nil || len(files) == 0 {
		t.Fatal("no alert rule files", err)
	}
	for _, path := range files {
		for field, longest := range map[string]*string{".title": &title, ".annotations.runbook_url // \"\"": &runbook} {
			values, err := exec.Command("yq", "-r", ".groups[].rules[] | "+field, path).Output()
			if err != nil {
				t.Fatal(err)
			}
			for _, value := range strings.Split(strings.TrimSpace(string(values)), "\n") {
				if len(value) > len(*longest) {
					*longest = value
				}
			}
		}
	}
	message = render("homelab.discord.message", many)
	if len(message) > 4096 || len(render("homelab.discord.title", many)) > 256 {
		t.Errorf("source title/runbook exceed Discord bounds: message=%d", len(message))
	}
}
