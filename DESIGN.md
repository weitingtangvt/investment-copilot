# Design System - Investment Assistant

## Product Context
- **What this is:** AI TextResearchText, TextResearchText, Text, TextReview, Text thesis TextResearchText. 
- **Who it's for:** Text, Text, TextResearchText, Text. 
- **Product posture:** Text, Text, Text dashboard, Text. 

## North Star
Text: 

- **Text**: TextResearchText, Text, Text, Text SaaS. 
- **Text**: Text, Text, Text, Text, TextTextText. 
- **Text**: Text, Text, Text. 

Text: 

> TextText, TextText AI Text. 

---

## Visual Direction

### Design Tone
- **Direction:** Buy-side / research desk / institutional note system
- **Mood:** Text, Text, Text, Text
- **Density:** Text, Text
- **Contrast strategy:** Text, Text, Text, Text, Text

### What It Should Feel Like
- TextResearchText, ResearchText, Text
- TextText, TextText
- Text AlphaSense / TextResearchText, Text App

### Hard Rules
- Text **light only**
- Text: **Text**
- Text, Text, Text, Text UI
- Text, Text: Text, Text, Text, Text, kv Text

---

## UI Principles

### 1. Data First
- Text
- TextTextText
- Text

### 2. Layout Before Decoration
- Text, Text, Text, Text
- Text, Text

### 3. Tables Over Cards
- TextResearchText table/list/dossier-first
- Text, Text

### 4. Language Discipline
- Text
- Text, Text page kicker Text research Text
- TextText

### 5. Color As Signal
- Text, Text
- Text
- TextResultText
- Text

---

## Color System

### Core Palette
Text CSS tokens Text. 

```css
:root {
  --bg-app: #f2f5f9;
  --bg-app-elevated: #edf2f7;
  --bg-surface: #ffffff;
  --bg-surface-muted: #f7f9fc;
  --bg-surface-soft: #f1f5f9;
  --bg-panel: #eef3f8;

  --text-primary: #122033;
  --text-secondary: #506176;
  --text-tertiary: #7b8898;
  --text-quaternary: #97a3b1;

  --border-subtle: #e3e9f0;
  --border-default: #d4dde7;
  --border-strong: #bcc9d7;

  --accent-primary: #245ac7;
  --accent-primary-hover: #1c4aa7;
  --accent-primary-soft: #eaf1ff;

  --positive: #0f7a45;
  --positive-soft: #edf8f1;
  --negative: #b92b3f;
  --negative-soft: #fbeff2;
  --warning: #a56a13;
  --warning-soft: #fbf4e8;
  --info-soft: #edf3fb;

  --shadow-card: 0 1px 0 rgba(18, 32, 51, 0.04), 0 8px 20px rgba(18, 32, 51, 0.04);
  --shadow-panel: 0 14px 34px rgba(18, 32, 51, 0.08);
  --shadow-modal: 0 20px 48px rgba(18, 32, 51, 0.12);
}
```

### Color Usage Rules
- Text: `--bg-app`
- Text: `--bg-surface`
- Text: `--bg-surface-muted`
- Text hover: Text `--bg-surface-soft`
- Text / Text / active indicator: `--accent-primary`
- StatusText: 
  - Text
  - RiskText
  - Status badge
  - Text

### Must Avoid
- Text, Text, Text
- Text
- Text

---

## Typography

### Font Stack
- Primary: `IBM Plex Sans`
- Numeric / ticker / date: `IBM Plex Mono`
- Chinese fallback: `PingFang SC`, `Microsoft YaHei`, `Noto Sans SC`

### Type Scale
Text: 

```css
--font-hero: 28px/36px;
--font-h1: 24px/32px;
--font-h2: 18px/26px;
--font-h3: 15px/22px;
--font-body: 14px/22px;
--font-body-sm: 13px/20px;
--font-label: 12px/18px;
--font-micro: 11px/16px;
```

### Weight Rules
- Hero / page title: `700`
- Section title: `600`
- Table header / field label / badge: `600`
- Body copy: `400`
- Numeric highlight: `600` Text `700`

### Typography Rules
- Text landing page Text, Text dossier Text
- section title Text, Text
- label Text, Text, TextResearchText
- Text mono; Text, ticker, Date, Text mono

---

## Spacing And Density

### Base Scale
- Base unit: `4px`
- Primary spacing scale:
  - `4`
  - `8`
  - `12`
  - `16`
  - `20`
  - `24`
  - `32`
  - `40`
  - `48`

### Density Rules
- Text: Text
- Text: Text
- Text padding

### Default Rhythm
- Page section gap: `24px`
- Section header to body: `0`
- Section body default padding: `18px - 20px`
- SAMPLE row gap: `10px - 12px`
- Table row height: `44px - 52px`
- KPI strip padding: `12px - 14px`

---

## Border, Radius, Shadow

### Radius
- Button: `6px`
- Input: `8px`
- Small badge/tag: `6px`
- Standard card: `10px`
- Large modal / drawer container: `14px`

### Border
- Text, Text
- DefaultText: `--border-default`
- Text, hover Text

### Shadow
- Text
- Text, Text
- TextText + Text

---

## Layout Templates

### A. Dossier Page
Text: TextDetails, ResearchText, Text

- Text: Text + meta strip
- Text: `8 / 4` Text `9 / 3`
- Text: Text thesis, Text, Text
- Text: Text, Text, Text, Status

### B. Table-first Page
Text: WatchText, Text, StockText

- Text: Text summary strip
- Text: Text
- Text: Text
- Text: TextDetailsTextDetails

### C. Review Page
Text: Weekly Review, TextAnalysis

- Text: This WeekText / CurrentText
- Text: Text
- Text: Text review queue
- Text: Text / Text

### D. Research Feed Page
Text: Text, Twitter, Text

- Text: Text
- Text: Summary / Text
- Text: Text
- Text, TextResearchText

---

## Component Spec

### 1. Page Header
- Text: kicker / title / subtitle / action cluster
- Text
- Text, Text 1 Text
- kicker Text, Text

### 2. Summary Strip
- Text KPI, Text
- TextSummaryTextText dashboard hero block
- Text, Text, Text

### 3. Section Card
- Text
- Text
- Text 2-4 Text section

### 4. List Row
- Text
- Text: Text / Text / Status / Text
- Text hover Text
- TextDefaultText, hover Text

### 5. Data Table
- Text
- ticker / Date / Text mono
- header Text, Text, Text
- Text
- Text sticky Text
- Text

### 6. Badge
- Text badge, Text pill
- Status badge Text 4 Text: 
  - Neutral
  - Positive
  - Negative
  - Warning
- badge Text, TextStatusText

### 7. Button
- Text
- Secondary Text ghost Text
- Text

### 8. Input
- Text, Text
- Text, Text
- Text outline, Text

### 9. Chart Container
- Text
- Text
- Text
- Text
- Text glow

### 10. Modal / Drawer
- Text drawer, Text modal
- Text modal
- Text, Text, TextDetails, Text drawer

### 11. Empty State
- Text, Text
- Text: 
  - Text
  - Text
- Text emoji, Text

---

## Page Recipes

### Weekly Review
- Text: 
  - CurrentText
  - Text
  - This WeekText
- TextResearchText, Text
- Text review Text

### Watchlist
- Text, Text
- Text, Text, Revisit Status
- TextDetailsText

### US Screener
- TextResultText
- CurrentText
- ResultText

### Stock Detail
- TextResearch dossier, Text notes app
- thesis Text
- TextResearchText, Text

### Wechat / Twitter
- Text research intake, Text
- Text, Text, TextStatus, SummaryResult, TextText

### Portfolio Analysis
- Text, Text
- Text, Text

---

## Writing Style
- TextResearchText, TextText
- Text: 
  - ResearchText
  - Text
  - Text
  - Text
  - SaveText
  - TextDate
  - Text
- Text: 
  - Text
  - TextGenerateText
  - Text
  - Text AI Analysis

---

## Anti-patterns

### Must Not Use
- Text
- Text
- Text
- Text
- Text
- Text
- Text
- Text
- TextStatus
- Text

### Smells Like AI Slop
- Text
- Text
- Text
- Text dashboard Text

---

## Implementation Priority

### P0
- Text base tokens
- Text badge Text
- Text
- TextResearchText

### P1
- WatchText table-first
- Text
- TextReviewText review material page

### P2
- Text dossier Text
- Text feed Text
- AnalysisTextText + TextText

---

## Decision Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-23 | Initial design system created | Text |
| 2026-03-30 | Moved implementation to light-only UI | Text |
| 2026-03-31 | Adopted Text as north star | TextResearchText |
