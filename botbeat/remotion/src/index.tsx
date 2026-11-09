/*
 Botbeat overlay (Remotion v4). Renders ONLY the graphics layer, transparent, 1080x1920 @ 30 fps:
 hook bar, word-timed captions, end CTA, clip credit, progress bar.
 make_short.py renders it as a PNG sequence and FFmpeg lays it over the footage exactly like the old PIL overlay.
 Props come from make_short.py: {hook, cta, credit, frames, words:[{t,d,w}]}  (t/d in seconds, as from edge-tts/OmniVoice)
*/
import React from 'react';
import {Composition, interpolate, registerRoot, spring, useCurrentFrame, useVideoConfig} from 'remotion';

type Word = {t: number; d: number; w: string};
type Props = {hook: string; cta: string; credit: string; frames: number; words: Word[]};

const FPS = 30;
const VO_DELAY = 0.15; // FFmpeg delays the voice by 150 ms (adelay=150)
const CTA_SECS = 2.6;  // same as the old overlay
const FONT = '"DejaVu Sans", Arial, sans-serif';
const YELLOW = 'rgb(255,214,0)';
const stroke = (px: number): React.CSSProperties => ({WebkitTextStroke: `${px}px #000`, paintOrder: 'stroke fill'});

type Chunk = {t0: number; t1: number; words: Word[]};
const chunks = (words: Word[], total: number): Chunk[] => {
  const groups: Word[][] = [];
  let cur: Word[] = [];
  for (const w of words) {
    cur.push(w);
    if (cur.length === 3 || /[.,?!]$/.test(w.w)) { groups.push(cur); cur = []; }
  }
  if (cur.length) groups.push(cur);
  return groups.map((g, i) => ({
    t0: g[0].t + VO_DELAY,
    t1: i + 1 < groups.length ? groups[i + 1][0].t + VO_DELAY : total,
    words: g,
  }));
};

const Hook: React.FC<{text: string}> = ({text}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame, fps, config: {damping: 14}});
  const long = text.length > 22;
  return (
    <div style={{
      position: 'absolute', top: 100, left: 50, right: 50, textAlign: 'center',
      transform: `translateY(${interpolate(s, [0, 1], [-260, 0])}px) scale(${interpolate(s, [0, 1], [0.9, 1])})`,
      background: YELLOW, color: '#111', borderRadius: 30, padding: '26px 34px',
      fontFamily: FONT, fontWeight: 700, fontSize: long ? 68 : 86, lineHeight: 1.08,
      boxShadow: '0 14px 44px rgba(0,0,0,.5)',
    }}>
      {text}
    </div>
  );
};

const Captions: React.FC<{words: Word[]; total: number}> = ({words, total}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const c = chunks(words, total).find((x) => t >= x.t0 && t < x.t1);
  if (!c) return null;
  const pop = spring({frame: frame - Math.round(c.t0 * fps), fps, config: {damping: 12, mass: 0.6}});
  const size = c.words.map((w) => w.w).join(' ').length <= 18 ? 84 : 66;
  return (
    <div style={{
      position: 'absolute', top: 1440, left: 30, right: 30, textAlign: 'center',
      transform: `scale(${interpolate(pop, [0, 1], [0.75, 1])})`,
    }}>
      {c.words.map((w, i) => {
        const on = t >= w.t + VO_DELAY && t < w.t + w.d + VO_DELAY + 0.05;
        return (
          <span key={i} style={{
            display: 'inline-block', margin: '0 14px', fontFamily: FONT, fontWeight: 700, fontSize: size,
            textTransform: 'uppercase', color: on ? YELLOW : '#fff', ...stroke(10),
            textShadow: '0 6px 18px rgba(0,0,0,.6)',
            transform: on ? 'translateY(-8px) scale(1.08)' : 'none',
          }}>
            {w.w}
          </span>
        );
      })}
    </div>
  );
};

const Cta: React.FC<{text: string; start: number}> = ({text, start}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: frame - start, fps, config: {damping: 10}});
  return (
    <div style={{
      position: 'absolute', top: 1420, left: 50, right: 50, textAlign: 'center',
      transform: `scale(${interpolate(s, [0, 1], [0.5, 1])})`, opacity: Math.min(1, s * 1.5),
      fontFamily: FONT, fontWeight: 700, fontSize: 88, lineHeight: 1.15, color: YELLOW, ...stroke(12),
      textShadow: '0 8px 22px rgba(0,0,0,.6)',
    }}>
      {text}
    </div>
  );
};

const Overlay: React.FC<Props> = ({hook, cta, credit, words}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const total = durationInFrames / fps;
  const ctaStart = Math.round((total - CTA_SECS) * fps);
  return (
    <div style={{position: 'absolute', inset: 0}}>
      <Hook text={hook} />
      {frame >= ctaStart ? <Cta text={cta} start={ctaStart} /> : <Captions words={words} total={total} />}
      <div style={{
        position: 'absolute', top: 1340, right: 30, fontFamily: FONT, fontWeight: 700, fontSize: 28,
        color: '#fff', ...stroke(4),
      }}>
        {credit}
      </div>
      <div style={{
        position: 'absolute', bottom: 0, left: 0, height: 12, background: YELLOW,
        width: `${(frame / Math.max(1, durationInFrames - 1)) * 100}%`,
      }} />
    </div>
  );
};

const Root: React.FC = () => (
  <Composition
    id="Overlay"
    component={Overlay}
    width={1080}
    height={1920}
    fps={FPS}
    durationInFrames={28 * FPS}
    defaultProps={{hook: 'THIS ROBOT CAN FOLD LAUNDRY', cta: 'WOULD YOU BUY ONE?', credit: 'clip: source', frames: 28 * FPS,
      words: [{t: 0, d: 0.4, w: 'This'}, {t: 0.4, d: 0.4, w: 'robot'}, {t: 0.8, d: 0.5, w: 'folds'}]}}
    calculateMetadata={({props}: {props: Props}) => ({durationInFrames: Math.max(1, Math.floor(props.frames))})}
  />
);

registerRoot(Root);
