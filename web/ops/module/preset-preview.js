// Illustrative firmware previews. No network or hardware writes; phase and timing are not measured.
export function previewKey(preview) {
  return JSON.stringify([preview?.name, preview?.style, preview?.speed]);
}

export function previewFrame(preview, count, elapsedSeconds) {
  if (!preview?.available || !['gradient', 'travel', 'pulse-rainbow', 'fade-black', 'jump'].includes(preview.style)) return null;
  const level = Math.max(0, Math.min(100, preview.brightness)) / 100;
  const speed = Math.max(-10, Math.min(10, preview.speed || 0));
  // An illustrative speed curve, deliberately labelled as unsynchronized in the UI.
  const step = Math.max(1, 8 * 2 ** (-speed / 3));
  const phase = Math.max(0, elapsedSeconds) / step;
  const scale = (color, gain = 1) => ({r: Math.round(color[0] * level * gain), g: Math.round(color[1] * level * gain), b: Math.round(color[2] * level * gain)});
  const rgb = [[255, 0, 0], [0, 255, 0], [0, 0, 255]];
  if (preview.style === 'jump' || preview.style === 'fade-black') {
    const color = rgb[Math.floor(phase) % 3];
    const gain = preview.style === 'fade-black' ? Math.sin(Math.PI * (phase % 1)) ** 2 : 1;
    return Array.from({length: count}, () => scale(color, gain));
  }
  const pairs = Math.max(1, Math.ceil(count / 2));
  // Travel moves existing colors between address positions; continuously changing each hue
  // looked like brightness pulsing rather than a pattern travelling along the wire.
  const shift = preview.style === 'travel' ? Math.floor(Math.max(0, elapsedSeconds) / (step / 10)) % Math.max(1, count) : 0;
  const gain = preview.style === 'pulse-rainbow' ? 0.15 + 0.85 * Math.sin(Math.PI * phase) ** 2 : 1;
  return Array.from({length: count}, (_, index) => {
    const sourceIndex = (index - shift + count) % Math.max(1, count);
    const hue = (Math.floor(sourceIndex / 2) / pairs) * 6;
    const x = 255 * (1 - Math.abs(hue % 2 - 1));
    const colors = [[255, x, 0], [x, 255, 0], [0, 255, x], [0, x, 255], [x, 0, 255], [255, 0, x]];
    return scale(colors[Math.floor(hue)] , gain);
  });
}

export function previewNotice(preview, paused) {
  if (!preview) return 'Preview from reported settings. Screen colors and brightness are approximate.';
  if (!preview.available) return 'No color model for this preset; grey means the preview is unavailable. Check the real light.';
  const behavior = {'travel': 'colors travel between LEDs', 'pulse-rainbow': 'colors pulse in place', 'fade-black': 'RGB fades through black', 'jump': 'sharp RGB changes'};
  if (preview.animated) return `${paused ? 'Animation paused' : 'Animated illustration'}: ${behavior[preview.style]}. Timing and phase are not synchronized with the light; Hubspace reports preset settings, not live LED frames.`;
  if (preview.style === 'gradient') return 'Approximate static rainbow gradient, shown in matching pairs as observed on this bench kit.';
  return 'Approximate preset illustration. Its colors and timing have not been verified on this kit.';
}
