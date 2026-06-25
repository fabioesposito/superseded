# Landing Page & Logo Polish — Design Spec

## Goal

Polish the existing retro/pixel-art landing page and SVG logo to feel "refined retro" — keep the CRT/arcade personality but make it cleaner, sharper, and more premium.

## Scope

Two files: `index.html` (landing page), `logo.svg` (standalone logo file). The inline SVG in the nav also needs updating to match.

## Logo Evolution — Colorful Shield + S

Keep the original colorful CRT shield aesthetic with the 5 colored vertical stripes. Replace the code brackets `</>` with a pixel-art "S" letter inside the shield.

### Shield
- Same shape as original: shield polygon with 5 colored stripes (red, orange, gold, green, cyan)
- Keep the scanlines inside the shield
- Keep the neon glow filter on the shield outline
- Keep the pixel-art corner accents

### S Letter
- Pixel-art "S" built from 4px rects, centered in the shield
- 6 columns wide, 7 rows tall (matching the letter proportions of the wordmark S)
- White fill with subtle glow filter
- 0.85 opacity

### Wordmark
- Keep the pixel-rect "SUPERSEDED" wordmark below the shield
- Keep the 5 colored accent dots
- Keep the "AI CODE REVIEW" subtitle

### Hero Version (index.html)
- Full wordmark + subtitle below the shield
- Subtle glow pulse animation on the shield (3s cycle, drop-shadow opacity 0.15 → 0.35)

### Footer Version (index.html)
- Shield only, no wordmark (too small to read)
- Stroke color muted to #555580 to match footer aesthetic
- S at 0.7 opacity

## Landing Page Polish

### Typography
- `Press Start 2P` — section labels only (.section-label, .hero-badge, nav links, .stage-num, .pass-tag)
- `VT323` — terminal/CRT areas only (.crt-body, .install-cmd, .server-term, .hero-sub, .section-sub)
- `Fira Code` — everything else (body, descriptions, card text, stage descriptions)
- Remove `image-rendering: pixelated` from `html` — it makes fonts ugly on some browsers

### Spacing & Layout
- Increase section padding from 80px to 100px
- Increase hero padding top from 140px to 160px
- More breathing room between stage items (gap 20px → 28px)
- CTA box padding 48px → 56px

### Color Refinement
- Primary neon colors stay for interactive/highlight only
- Muted variants for body text: use existing --text-muted (#8888b8) more consistently
- Background card color slightly lighter: #10101f → #12122a for better contrast
- Border color refined: #282850 → #2a2a55

### CRT Overlay
- Thinner scanlines: 3px spacing → 4px spacing, lower opacity (0.6 → 0.4)
- Remove the vignette from the CRT overlay (keep it only on the terminal demo)
- Reduce body::after z-index concern — it currently blocks some interactions

### Animations & Interactivity
- **Scroll fade-in**: Add `.reveal` class with IntersectionObserver — elements fade in + slide up 20px on scroll
- **Button hover**: Add subtle glow pulse animation on primary buttons (box-shadow pulse, 0.8s)
- **Card hover**: Power-up cards get a subtle top-border color accent on hover (shift from border to cyan/green)
- **Stage numbers**: Pulse glow on hover
- **Pass tags**: Scale up slightly (1.05x) on hover with glow
- **Hero badge dot**: Keep existing pulse animation
- **Install command**: Subtle typing cursor animation in the command area
- **Smooth scroll**: Already present, keep it

### Hero Section
- Replace the pixel-sprite treasure chest with the logo rendered at larger scale (the shield + brackets)
- The pixel-sprite is charming but feels disconnected from the brand — the logo is stronger
- Keep the badge, keep the headline, keep the install command

### Terminal Demo
- Keep the CRT aesthetic but clean up:
  - Slightly less aggressive scanline overlay
  - Better line spacing
  - Add a subtle typing animation for the command lines (CSS animation, not JS)

### Footer
- Add the logo (small version) to the left side of the footer

## Implementation Order

1. Logo SVG evolution (standalone file)
2. Update inline nav logo to match
3. Typography changes
4. Spacing/layout refinements
5. Color refinements
6. CRT overlay cleanup
7. Scroll animations (IntersectionObserver)
8. Hover state upgrades
9. Hero section (replace pixel-sprite with logo)
10. Terminal demo refinements
11. Footer logo addition
12. Test with playwright-cli

## Files Modified

- `logo.svg` — standalone logo file
- `index.html` — all landing page changes (inline styles + HTML structure + JS)

## Out of Scope

- Content/copy changes (headlines, descriptions stay the same)
- New sections or pages
- Backend/CLI changes
- Mobile-specific redesign (responsive tweaks OK)
