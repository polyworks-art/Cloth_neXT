(() => {
  'use strict';

  const root = document.documentElement;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const finePointer = window.matchMedia('(pointer:fine)').matches;
  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
  const lerp = (a, b, t) => a + (b - a) * t;

  document.querySelectorAll('[data-year]').forEach((node) => {
    node.textContent = new Date().getFullYear();
  });

  const header = document.querySelector('[data-header]');
  const progressBar = document.querySelector('.scroll-progress span');
  const updateScroll = () => {
    const scrollTop = window.scrollY || document.documentElement.scrollTop;
    const maxScroll = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
    if (progressBar) progressBar.style.transform = `scaleX(${clamp(scrollTop / maxScroll, 0, 1)})`;
    header?.classList.toggle('scrolled', scrollTop > 24);
  };
  updateScroll();
  window.addEventListener('scroll', updateScroll, { passive: true });

  const cursorLight = document.querySelector('.cursor-light');
  if (cursorLight && finePointer && !reducedMotion) {
    window.addEventListener('pointermove', (event) => {
      cursorLight.style.setProperty('--x', `${event.clientX}px`);
      cursorLight.style.setProperty('--y', `${event.clientY}px`);
      cursorLight.classList.add('visible');
    }, { passive: true });
  }

  const reveals = document.querySelectorAll('.reveal');
  if (reducedMotion || !('IntersectionObserver' in window)) {
    reveals.forEach((node) => node.classList.add('revealed'));
  } else {
    const revealObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('revealed');
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.11, rootMargin: '0px 0px -7% 0px' });
    reveals.forEach((node) => revealObserver.observe(node));
  }

  document.querySelectorAll('.magnetic').forEach((element) => {
    if (!finePointer || reducedMotion) return;
    element.addEventListener('pointermove', (event) => {
      const rect = element.getBoundingClientRect();
      const x = event.clientX - rect.left - rect.width / 2;
      const y = event.clientY - rect.top - rect.height / 2;
      element.style.transform = `translate(${x * 0.07}px, ${y * 0.1}px)`;
    });
    element.addEventListener('pointerleave', () => { element.style.transform = ''; });
  });

  document.querySelectorAll('.tilt-card').forEach((card) => {
    if (!finePointer || reducedMotion) return;
    card.addEventListener('pointermove', (event) => {
      const rect = card.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      card.style.setProperty('--tilt-x', `${x}`);
      card.style.setProperty('--tilt-y', `${y}`);
      card.style.setProperty('--glow-x', `${(x + 0.5) * 100}%`);
      card.style.setProperty('--glow-y', `${(y + 0.5) * 100}%`);
    });
    card.addEventListener('pointerleave', () => {
      card.style.setProperty('--tilt-x', '0');
      card.style.setProperty('--tilt-y', '0');
    });
  });

  document.querySelectorAll('[data-count]').forEach((node) => {
    const target = Number(node.dataset.count || 0);
    if (!target || reducedMotion) return;
    let played = false;
    const observer = new IntersectionObserver((entries) => {
      if (played || !entries[0]?.isIntersecting) return;
      played = true;
      const start = performance.now();
      const tick = (time) => {
        const t = clamp((time - start) / 1100, 0, 1);
        const eased = 1 - Math.pow(1 - t, 4);
        node.textContent = String(Math.round(target * eased));
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
      observer.disconnect();
    }, { threshold: .5 });
    observer.observe(node);
  });

  class CanvasHost {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas?.getContext('2d', { alpha: true }) || null;
      this.width = 0;
      this.height = 0;
      this.dpr = 1;
      this.visible = true;
      if (!this.canvas || !this.ctx) return;
      this.resize = this.resize.bind(this);
      this.resize();
      if ('ResizeObserver' in window) new ResizeObserver(this.resize).observe(this.canvas);
      else window.addEventListener('resize', this.resize, { passive: true });
      if ('IntersectionObserver' in window) {
        new IntersectionObserver((entries) => {
          this.visible = entries[0]?.isIntersecting ?? true;
        }, { rootMargin: '180px' }).observe(this.canvas);
      }
    }

    resize() {
      if (!this.canvas || !this.ctx) return;
      const rect = this.canvas.getBoundingClientRect();
      this.width = Math.max(1, rect.width);
      this.height = Math.max(1, rect.height);
      this.dpr = Math.min(window.devicePixelRatio || 1, 1.75);
      this.canvas.width = Math.round(this.width * this.dpr);
      this.canvas.height = Math.round(this.height * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.onResize?.();
    }

    clear() {
      this.ctx?.clearRect(0, 0, this.width, this.height);
    }
  }

  const makePoint = (x, y, pinned = false) => ({ x, y, oldX: x, oldY: y, pinned, pinX: x, pinY: y });
  const makeConstraint = (a, b, rest, stiffness = 1) => ({ a, b, rest, stiffness });

  function integrate(points, gravity = 0.34, damping = .992) {
    points.forEach((point) => {
      if (point.pinned) {
        point.x = point.pinX;
        point.y = point.pinY;
        point.oldX = point.pinX;
        point.oldY = point.pinY;
        return;
      }
      const vx = (point.x - point.oldX) * damping;
      const vy = (point.y - point.oldY) * damping;
      point.oldX = point.x;
      point.oldY = point.y;
      point.x += vx;
      point.y += vy + gravity;
    });
  }

  function solveConstraints(points, constraints, iterations = 5) {
    for (let iteration = 0; iteration < iterations; iteration += 1) {
      constraints.forEach((constraint) => {
        const a = points[constraint.a];
        const b = points[constraint.b];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const distance = Math.hypot(dx, dy) || .0001;
        const delta = ((distance - constraint.rest) / distance) * constraint.stiffness;
        const ax = dx * delta * .5;
        const ay = dy * delta * .5;
        if (!a.pinned) { a.x += ax; a.y += ay; }
        if (!b.pinned) { b.x -= ax; b.y -= ay; }
      });
      points.forEach((point) => {
        if (!point.pinned) return;
        point.x = point.pinX;
        point.y = point.pinY;
      });
    }
  }

  function collideEllipse(point, ellipse, padding = 1) {
    const dx = point.x - ellipse.x;
    const dy = point.y - ellipse.y;
    const rx = ellipse.rx + padding;
    const ry = ellipse.ry + padding;
    const normalized = (dx * dx) / (rx * rx) + (dy * dy) / (ry * ry);
    if (normalized >= 1) return false;
    const scale = 1 / Math.sqrt(normalized || .0001);
    point.x = ellipse.x + dx * scale;
    point.y = ellipse.y + dy * scale;
    return true;
  }

  function keepInBounds(points, width, height, margin = 8, bounce = .12) {
    points.forEach((point) => {
      if (point.pinned) return;
      if (point.x < margin) { point.x = margin; point.oldX = point.x + (point.x - point.oldX) * bounce; }
      if (point.x > width - margin) { point.x = width - margin; point.oldX = point.x + (point.x - point.oldX) * bounce; }
      if (point.y < margin) { point.y = margin; point.oldY = point.y + (point.y - point.oldY) * bounce; }
      if (point.y > height - margin) { point.y = height - margin; point.oldY = point.y + (point.y - point.oldY) * bounce; }
    });
  }

  function drawGridCloth(ctx, points, columns, rows, options = {}) {
    const line = options.line || 'rgba(116,255,216,.20)';
    const bright = options.bright || 'rgba(164,255,229,.48)';
    const fill = options.fill || 'rgba(68,221,173,.045)';
    ctx.save();
    if (fill) {
      for (let row = 0; row < rows - 1; row += 1) {
        for (let col = 0; col < columns - 1; col += 1) {
          const a = points[row * columns + col];
          const b = points[row * columns + col + 1];
          const c = points[(row + 1) * columns + col + 1];
          const d = points[(row + 1) * columns + col];
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.lineTo(c.x, c.y);
          ctx.lineTo(d.x, d.y);
          ctx.closePath();
          ctx.fillStyle = fill;
          ctx.fill();
        }
      }
    }
    ctx.lineWidth = .75;
    for (let row = 0; row < rows; row += 1) {
      ctx.beginPath();
      for (let col = 0; col < columns; col += 1) {
        const point = points[row * columns + col];
        col === 0 ? ctx.moveTo(point.x, point.y) : ctx.lineTo(point.x, point.y);
      }
      ctx.strokeStyle = row % 4 === 0 ? bright : line;
      ctx.stroke();
    }
    for (let col = 0; col < columns; col += 1) {
      ctx.beginPath();
      for (let row = 0; row < rows; row += 1) {
        const point = points[row * columns + col];
        row === 0 ? ctx.moveTo(point.x, point.y) : ctx.lineTo(point.x, point.y);
      }
      ctx.strokeStyle = col % 5 === 0 ? bright : line;
      ctx.stroke();
    }
    points.forEach((point) => {
      if (!point.pinned) return;
      ctx.beginPath();
      ctx.arc(point.x, point.y, 4, 0, Math.PI * 2);
      ctx.fillStyle = '#a8ffe2';
      ctx.shadowColor = '#65f5c9';
      ctx.shadowBlur = 12;
      ctx.fill();
      ctx.shadowBlur = 0;
    });
    ctx.restore();
  }

  function buildCloth(columns, rows, originX, originY, spacingX, spacingY, pinRule) {
    const points = [];
    const constraints = [];
    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < columns; col += 1) {
        const pinned = Boolean(pinRule?.(col, row, columns, rows));
        points.push(makePoint(originX + col * spacingX, originY + row * spacingY, pinned));
      }
    }
    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < columns; col += 1) {
        const index = row * columns + col;
        if (col < columns - 1) constraints.push(makeConstraint(index, index + 1, spacingX, .94));
        if (row < rows - 1) constraints.push(makeConstraint(index, index + columns, spacingY, .94));
        if (col < columns - 1 && row < rows - 1) {
          constraints.push(makeConstraint(index, index + columns + 1, Math.hypot(spacingX, spacingY), .22));
        }
      }
    }
    return { points, constraints, columns, rows };
  }

  function bindPointer(canvas, handler) {
    if (!canvas) return;
    let active = false;
    let lastX = 0;
    let lastY = 0;
    const position = (event) => {
      const rect = canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };
    canvas.addEventListener('pointerdown', (event) => {
      active = true;
      canvas.setPointerCapture?.(event.pointerId);
      const pos = position(event);
      lastX = pos.x;
      lastY = pos.y;
      handler(pos.x, pos.y, 0, 0, true);
    });
    canvas.addEventListener('pointermove', (event) => {
      const pos = position(event);
      const dx = pos.x - lastX;
      const dy = pos.y - lastY;
      handler(pos.x, pos.y, dx, dy, active);
      lastX = pos.x;
      lastY = pos.y;
    });
    const release = (event) => {
      active = false;
      canvas.releasePointerCapture?.(event.pointerId);
    };
    canvas.addEventListener('pointerup', release);
    canvas.addEventListener('pointercancel', release);
    canvas.addEventListener('pointerleave', (event) => { if (!event.buttons) active = false; });
  }

  class HeroSimulation extends CanvasHost {
    constructor(canvas) {
      super(canvas);
      this.cloth = null;
      this.colliders = [];
      this.pointer = { x: -999, y: -999, active: false };
      this.phase = 0;
      this.onResize = () => this.build();
      this.build();
      bindPointer(canvas, (x, y, dx, dy, active) => {
        this.pointer = { x, y, active };
        if (!active || !this.cloth) return;
        this.cloth.points.forEach((point) => {
          const distance = Math.hypot(point.x - x, point.y - y);
          if (distance > 85 || point.pinned) return;
          const falloff = 1 - distance / 85;
          point.x += dx * falloff * 1.2;
          point.y += dy * falloff * 1.2;
          point.oldX -= dx * falloff * .08;
          point.oldY -= dy * falloff * .08;
        });
      });
      this.canvas?.addEventListener('pointerleave', () => { this.pointer.active = false; });
      requestAnimationFrame((time) => this.frame(time));
    }

    build() {
      if (!this.width || !this.height) return;
      const columns = 20;
      const rows = 17;
      const spacingX = Math.min(23, this.width * .032);
      const spacingY = Math.min(22, this.height * .032);
      const clothWidth = spacingX * (columns - 1);
      const originX = this.width * .52 - clothWidth * .5;
      const originY = this.height * .18;
      this.cloth = buildCloth(columns, rows, originX, originY, spacingX, spacingY, (col, row) => row === 0 && (col === 2 || col === columns - 3));
      this.colliders = [
        { x: this.width * .51, y: this.height * .30, rx: this.width * .055, ry: this.height * .064 },
        { x: this.width * .51, y: this.height * .49, rx: this.width * .145, ry: this.height * .21 },
        { x: this.width * .37, y: this.height * .49, rx: this.width * .052, ry: this.height * .19, rotation: .16 },
        { x: this.width * .65, y: this.height * .49, rx: this.width * .052, ry: this.height * .19, rotation: -.16 }
      ];
    }

    disturb() {
      if (!this.cloth) return;
      this.cloth.points.forEach((point) => {
        if (point.pinned) return;
        const weight = Math.max(0, 1 - Math.hypot(point.x - this.width * .52, point.y - this.height * .43) / (this.width * .45));
        point.oldX -= (Math.random() - .35) * 14 * weight;
        point.oldY += Math.random() * 4 * weight;
      });
    }

    update(time) {
      if (!this.cloth) return;
      this.phase = time * .001;
      integrate(this.cloth.points, .31, .993);
      this.cloth.points.forEach((point, index) => {
        if (point.pinned) return;
        const row = Math.floor(index / this.cloth.columns);
        point.x += Math.sin(this.phase * .8 + row * .25) * .018;
      });
      solveConstraints(this.cloth.points, this.cloth.constraints, 6);
      for (let pass = 0; pass < 3; pass += 1) {
        this.cloth.points.forEach((point) => this.colliders.forEach((ellipse) => collideEllipse(point, ellipse, 3)));
        solveConstraints(this.cloth.points, this.cloth.constraints, 2);
      }
      keepInBounds(this.cloth.points, this.width, this.height - 72, 8);
    }

    drawColliderCage(ctx) {
      ctx.save();
      ctx.setLineDash([7, 8]);
      ctx.lineWidth = 1;
      this.colliders.forEach((ellipse, index) => {
        ctx.beginPath();
        ctx.ellipse(ellipse.x, ellipse.y, ellipse.rx, ellipse.ry, ellipse.rotation || 0, 0, Math.PI * 2);
        ctx.fillStyle = index === 1 ? 'rgba(101,245,201,.035)' : 'rgba(101,245,201,.018)';
        ctx.fill();
        ctx.strokeStyle = index === 1 ? 'rgba(101,245,201,.34)' : 'rgba(101,245,201,.20)';
        ctx.stroke();
      });
      ctx.setLineDash([]);
      const joints = [
        [this.width * .51, this.height * .36],
        [this.width * .40, this.height * .37],
        [this.width * .62, this.height * .37],
        [this.width * .43, this.height * .64],
        [this.width * .59, this.height * .64]
      ];
      joints.forEach(([x, y], index) => {
        ctx.beginPath();
        ctx.arc(x, y, index === 0 ? 4 : 3, 0, Math.PI * 2);
        ctx.fillStyle = '#65f5c9';
        ctx.shadowColor = '#65f5c9';
        ctx.shadowBlur = 12;
        ctx.fill();
      });
      ctx.restore();
    }

    draw(time) {
      const ctx = this.ctx;
      this.clear();
      ctx.save();
      this.drawColliderCage(ctx);
      drawGridCloth(ctx, this.cloth.points, this.cloth.columns, this.cloth.rows, {
        line: 'rgba(105,245,203,.19)',
        bright: 'rgba(171,255,232,.48)',
        fill: 'rgba(60,225,172,.045)'
      });
      if (!this.pointer.active) {
        const x = this.width * (.5 + Math.sin(time * .00035) * .18);
        const y = this.height * (.42 + Math.cos(time * .00042) * .09);
        ctx.beginPath();
        ctx.arc(x, y, 18, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(101,245,201,.18)';
        ctx.stroke();
      }
      ctx.restore();
    }

    frame(time) {
      requestAnimationFrame((next) => this.frame(next));
      if (!this.visible || document.hidden) return;
      this.update(time);
      this.draw(time);
    }
  }

  const heroCanvas = document.querySelector('#hero-canvas');
  const heroSimulation = heroCanvas && !reducedMotion ? new HeroSimulation(heroCanvas) : null;
  document.querySelector('[data-nudge-hero]')?.addEventListener('click', () => heroSimulation?.disturb());
  const heroViewport = document.querySelector('[data-hero-viewport]');
  if (heroViewport && finePointer && !reducedMotion) {
    heroViewport.addEventListener('pointermove', (event) => {
      const rect = heroViewport.getBoundingClientRect();
      heroViewport.style.setProperty('--px', `${(event.clientX - rect.left) / rect.width - .5}`);
      heroViewport.style.setProperty('--py', `${(event.clientY - rect.top) / rect.height - .5}`);
    });
    heroViewport.addEventListener('pointerleave', () => {
      heroViewport.style.setProperty('--px', '0');
      heroViewport.style.setProperty('--py', '0');
    });
  }
  const heroFrame = document.querySelector('[data-hero-frame]');
  if (heroFrame && !reducedMotion) {
    let frame = 84;
    window.setInterval(() => {
      frame = frame >= 160 ? 84 : frame + 1;
      heroFrame.textContent = String(frame).padStart(3, '0');
    }, 210);
  }

  const roleData = {
    cloth: {
      overline: 'LAYERED CONTACT',
      title: 'Fabric that can fold, hang and meet itself.',
      description: 'A pinned cloth patch reacts to gravity, drag and contact while preserving the readable behavior of a real textile surface.',
      points: ['Self and cloth-to-cloth contact', 'Hard and yielding soft pins', 'Material-driven stretch and bend'],
      instruction: 'Drag through the cloth'
    },
    rod: {
      overline: 'ROD / CABLE DYNAMICS',
      title: 'A flexible line suspended between real anchors.',
      description: 'The cable sags under gravity, carries waves across its length and keeps both endpoints fixed instead of pretending to be a floating ribbon.',
      points: ['Curve-based cable setup', 'Fixed or animated endpoints', 'Shared collision and bake workflow'],
      instruction: 'Pull the hanging cable'
    },
    soft: {
      overline: 'VOLUME RESPONSE',
      title: 'A squashy volume that actually deforms.',
      description: 'The soft body falls, compresses against the floor, rebounds and slowly restores its shape through internal volume constraints.',
      points: ['Automatic volume preparation', 'Collision-aware deformation', 'Bounce, squash and recovery'],
      instruction: 'Poke the soft body'
    },
    collider: {
      overline: 'VISIBLE CONTACT',
      title: 'Cloth displaced by the geometry it hits.',
      description: 'A moving collider pushes through a falling cloth sheet so the purpose is visible immediately: contact changes the fabric trajectory.',
      points: ['Static and animated modes', 'Character collision cages', 'Dense motion sampling'],
      instruction: 'Move the collider'
    },
    force: {
      overline: 'DIRECTED FORCE FIELD',
      title: 'Motion follows the field, not random decoration.',
      description: 'Streamlines and cloth ribbons bend around a controllable force source, making direction, falloff and turbulence readable at a glance.',
      points: ['Gravity and wind controls', 'Directional and radial fields', 'Keyframe-ready intensity'],
      instruction: 'Move the force source'
    }
  };

  class RoleSimulation extends CanvasHost {
    constructor(canvas) {
      super(canvas);
      this.mode = 'cloth';
      this.pointer = { x: -999, y: -999, dx: 0, dy: 0, active: false };
      this.points = [];
      this.constraints = [];
      this.columns = 0;
      this.rows = 0;
      this.blob = null;
      this.particles = [];
      this.time = 0;
      this.onResize = () => this.reset();
      this.reset();
      bindPointer(canvas, (x, y, dx, dy, active) => {
        this.pointer = { x, y, dx, dy, active };
        if (this.mode === 'collider') return;
        const radius = this.mode === 'rod' ? 70 : 95;
        this.points.forEach((point) => {
          if (point.pinned) return;
          const distance = Math.hypot(point.x - x, point.y - y);
          if (distance > radius) return;
          const falloff = 1 - distance / radius;
          point.x += dx * falloff * 1.25;
          point.y += dy * falloff * 1.25;
          if (this.mode === 'soft' && active) {
            point.oldX -= dx * falloff * .35;
            point.oldY -= dy * falloff * .35;
          }
        });
      });
      canvas?.addEventListener('pointerleave', () => { this.pointer.active = false; });
      requestAnimationFrame((time) => this.frame(time));
    }

    setMode(mode) {
      this.mode = mode;
      this.reset();
    }

    reset() {
      if (!this.width || !this.height) return;
      this.points = [];
      this.constraints = [];
      this.blob = null;
      this.particles = [];
      if (this.mode === 'cloth') this.initCloth();
      if (this.mode === 'rod') this.initRod();
      if (this.mode === 'soft') this.initSoft();
      if (this.mode === 'collider') this.initCollider();
      if (this.mode === 'force') this.initForce();
    }

    visualArea() {
      const mobile = this.width < 760;
      return mobile
        ? { left: this.width * .08, right: this.width * .92, top: this.height * .08, bottom: this.height * .56 }
        : { left: this.width * .48, right: this.width * .95, top: this.height * .1, bottom: this.height * .9 };
    }

    initCloth() {
      const area = this.visualArea();
      const columns = 18;
      const rows = 15;
      const sx = (area.right - area.left) / (columns - 1);
      const sy = Math.min((area.bottom - area.top) / (rows - 1), sx * .92);
      const built = buildCloth(columns, rows, area.left, area.top + 16, sx, sy, (col, row) => row === 0 && (col === 0 || col === columns - 1));
      Object.assign(this, built);
    }

    initRod() {
      const area = this.visualArea();
      const count = 22;
      const startX = area.left + 8;
      const endX = area.right - 8;
      const y = area.top + (area.bottom - area.top) * .2;
      const spacing = (endX - startX) / (count - 1);
      for (let index = 0; index < count; index += 1) {
        const point = makePoint(startX + index * spacing, y + Math.sin(index / (count - 1) * Math.PI) * 70, index === 0 || index === count - 1);
        this.points.push(point);
        if (index > 0) this.constraints.push(makeConstraint(index - 1, index, spacing, .99));
      }
      for (let index = 0; index < count - 2; index += 1) this.constraints.push(makeConstraint(index, index + 2, spacing * 2, .35));
    }

    initSoft() {
      const area = this.visualArea();
      const count = 26;
      const centerX = lerp(area.left, area.right, .58);
      const centerY = area.top + 55;
      const radiusX = Math.min(105, (area.right - area.left) * .25);
      const radiusY = Math.min(92, (area.bottom - area.top) * .22);
      const centerIndex = count;
      for (let index = 0; index < count; index += 1) {
        const angle = index / count * Math.PI * 2;
        this.points.push(makePoint(centerX + Math.cos(angle) * radiusX, centerY + Math.sin(angle) * radiusY));
      }
      this.points.push(makePoint(centerX, centerY));
      for (let index = 0; index < count; index += 1) {
        const next = (index + 1) % count;
        const skip = (index + 2) % count;
        this.constraints.push(makeConstraint(index, next, Math.hypot(this.points[index].x - this.points[next].x, this.points[index].y - this.points[next].y), .9));
        this.constraints.push(makeConstraint(index, skip, Math.hypot(this.points[index].x - this.points[skip].x, this.points[index].y - this.points[skip].y), .38));
        this.constraints.push(makeConstraint(index, centerIndex, Math.hypot(this.points[index].x - centerX, this.points[index].y - centerY), .3));
      }
      this.blob = { count, centerIndex, floor: area.bottom - 8 };
      this.points.forEach((point) => { point.oldY -= 1.8; });
    }

    initCollider() {
      const area = this.visualArea();
      const columns = 18;
      const rows = 16;
      const sx = (area.right - area.left) / (columns - 1);
      const sy = Math.min(sx * .85, (area.bottom - area.top) / (rows - 1));
      const built = buildCloth(columns, rows, area.left, area.top, sx, sy, (col, row) => row === 0 && (col === 0 || col === columns - 1));
      Object.assign(this, built);
      this.collider = { x: lerp(area.left, area.right, .55), y: lerp(area.top, area.bottom, .58), rx: Math.min(75, (area.right - area.left) * .16), ry: Math.min(75, (area.bottom - area.top) * .17) };
    }

    initForce() {
      const area = this.visualArea();
      this.forceSource = { x: lerp(area.left, area.right, .64), y: lerp(area.top, area.bottom, .5) };
      for (let index = 0; index < 150; index += 1) {
        this.particles.push({
          x: area.left + Math.random() * (area.right - area.left),
          y: area.top + Math.random() * (area.bottom - area.top),
          oldX: 0,
          oldY: 0,
          life: Math.random() * 1,
          speed: .6 + Math.random() * 1.2
        });
      }
    }

    updateCloth() {
      integrate(this.points, .34, .993);
      solveConstraints(this.points, this.constraints, 6);
      keepInBounds(this.points, this.width, this.height, 8);
    }

    updateRod() {
      integrate(this.points, .28, .995);
      this.points.forEach((point, index) => {
        if (!point.pinned) point.x += Math.sin(this.time * 1.5 + index * .28) * .018;
      });
      solveConstraints(this.points, this.constraints, 8);
      keepInBounds(this.points, this.width, this.height, 8);
    }

    updateSoft() {
      integrate(this.points, .38, .991);
      const center = this.points[this.blob.centerIndex];
      center.oldX -= Math.sin(this.time * 1.1) * .015;
      solveConstraints(this.points, this.constraints, 8);
      this.points.slice(0, this.blob.count).forEach((point) => {
        if (point.y > this.blob.floor) {
          point.y = this.blob.floor;
          point.oldY = point.y + Math.abs(point.y - point.oldY) * .32;
          point.oldX += (point.x - point.oldX) * .08;
        }
      });
      const area = this.visualArea();
      if (center.y > area.bottom - 90) center.oldY += 1.2;
      keepInBounds(this.points, this.width, this.height, 8, .22);
    }

    updateCollider() {
      integrate(this.points, .34, .993);
      const area = this.visualArea();
      const targetX = this.pointer.active ? clamp(this.pointer.x, area.left + 45, area.right - 45) : lerp(area.left, area.right, .54) + Math.sin(this.time * .85) * (area.right - area.left) * .17;
      const targetY = this.pointer.active ? clamp(this.pointer.y, area.top + 45, area.bottom - 45) : lerp(area.top, area.bottom, .58) + Math.cos(this.time * .58) * 18;
      this.collider.x = lerp(this.collider.x, targetX, .09);
      this.collider.y = lerp(this.collider.y, targetY, .09);
      solveConstraints(this.points, this.constraints, 5);
      for (let pass = 0; pass < 4; pass += 1) {
        this.points.forEach((point) => collideEllipse(point, this.collider, 3));
        solveConstraints(this.points, this.constraints, 1);
      }
      keepInBounds(this.points, this.width, this.height, 8);
    }

    updateForce() {
      const area = this.visualArea();
      if (this.pointer.active) {
        this.forceSource.x = clamp(this.pointer.x, area.left, area.right);
        this.forceSource.y = clamp(this.pointer.y, area.top, area.bottom);
      } else {
        this.forceSource.x = lerp(area.left, area.right, .62) + Math.sin(this.time * .7) * 38;
        this.forceSource.y = lerp(area.top, area.bottom, .52) + Math.cos(this.time * .9) * 24;
      }
      this.particles.forEach((particle) => {
        particle.oldX = particle.x;
        particle.oldY = particle.y;
        const dx = particle.x - this.forceSource.x;
        const dy = particle.y - this.forceSource.y;
        const distance = Math.hypot(dx, dy) || 1;
        const influence = clamp(1 - distance / 240, 0, 1);
        const tangentX = -dy / distance;
        const tangentY = dx / distance;
        particle.x += 1.35 * particle.speed + tangentX * influence * 2.2;
        particle.y += Math.sin(this.time * 2 + particle.x * .018) * .34 + tangentY * influence * 2.2;
        particle.life += .004 * particle.speed;
        if (particle.x > area.right || particle.y < area.top || particle.y > area.bottom || particle.life > 1.25) {
          particle.x = area.left;
          particle.y = area.top + Math.random() * (area.bottom - area.top);
          particle.oldX = particle.x - 4;
          particle.oldY = particle.y;
          particle.life = 0;
        }
      });
    }

    update(time) {
      this.time = time * .001;
      if (this.mode === 'cloth') this.updateCloth();
      if (this.mode === 'rod') this.updateRod();
      if (this.mode === 'soft') this.updateSoft();
      if (this.mode === 'collider') this.updateCollider();
      if (this.mode === 'force') this.updateForce();
    }

    drawCloth() {
      drawGridCloth(this.ctx, this.points, this.columns, this.rows, {
        line: 'rgba(101,245,201,.20)', bright: 'rgba(178,255,232,.54)', fill: 'rgba(69,224,176,.04)'
      });
    }

    drawRod() {
      const ctx = this.ctx;
      ctx.save();
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      this.points.forEach((point, index) => index === 0 ? ctx.moveTo(point.x, point.y) : ctx.lineTo(point.x, point.y));
      ctx.strokeStyle = 'rgba(101,245,201,.18)';
      ctx.lineWidth = 12;
      ctx.stroke();
      ctx.strokeStyle = '#83f7d1';
      ctx.shadowColor = '#65f5c9';
      ctx.shadowBlur = 14;
      ctx.lineWidth = 2.2;
      ctx.stroke();
      ctx.shadowBlur = 0;
      this.points.forEach((point, index) => {
        if (index % 3 !== 0 && !point.pinned) return;
        ctx.beginPath();
        ctx.arc(point.x, point.y, point.pinned ? 7 : 2.4, 0, Math.PI * 2);
        ctx.fillStyle = point.pinned ? '#b7ffe8' : '#65f5c9';
        ctx.fill();
        if (point.pinned) {
          ctx.beginPath();
          ctx.arc(point.x, point.y, 13, 0, Math.PI * 2);
          ctx.strokeStyle = 'rgba(101,245,201,.25)';
          ctx.stroke();
        }
      });
      ctx.restore();
    }

    drawSoft() {
      const ctx = this.ctx;
      const count = this.blob.count;
      ctx.save();
      ctx.beginPath();
      for (let index = 0; index < count; index += 1) {
        const previous = this.points[(index - 1 + count) % count];
        const current = this.points[index];
        const midX = (previous.x + current.x) * .5;
        const midY = (previous.y + current.y) * .5;
        index === 0 ? ctx.moveTo(midX, midY) : ctx.quadraticCurveTo(previous.x, previous.y, midX, midY);
      }
      ctx.closePath();
      const center = this.points[this.blob.centerIndex];
      const gradient = ctx.createRadialGradient(center.x - 30, center.y - 35, 8, center.x, center.y, 130);
      gradient.addColorStop(0, 'rgba(183,255,232,.72)');
      gradient.addColorStop(.4, 'rgba(74,226,178,.34)');
      gradient.addColorStop(1, 'rgba(18,86,68,.18)');
      ctx.fillStyle = gradient;
      ctx.shadowColor = 'rgba(101,245,201,.28)';
      ctx.shadowBlur = 30;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = 'rgba(171,255,230,.62)';
      ctx.lineWidth = 1.4;
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(this.visualArea().left, this.blob.floor + 1);
      ctx.lineTo(this.visualArea().right, this.blob.floor + 1);
      ctx.strokeStyle = 'rgba(101,245,201,.18)';
      ctx.setLineDash([5, 7]);
      ctx.stroke();
      ctx.restore();
    }

    drawCollider() {
      drawGridCloth(this.ctx, this.points, this.columns, this.rows, {
        line: 'rgba(101,245,201,.17)', bright: 'rgba(177,255,232,.47)', fill: 'rgba(69,224,176,.033)'
      });
      const ctx = this.ctx;
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(this.collider.x, this.collider.y, this.collider.rx, this.collider.ry, 0, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(101,214,255,.09)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(101,214,255,.66)';
      ctx.lineWidth = 1.4;
      ctx.shadowColor = 'rgba(101,214,255,.45)';
      ctx.shadowBlur = 18;
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.beginPath();
      ctx.arc(this.collider.x, this.collider.y, 4, 0, Math.PI * 2);
      ctx.fillStyle = '#9ee8ff';
      ctx.fill();
      ctx.restore();
    }

    drawForce() {
      const ctx = this.ctx;
      const area = this.visualArea();
      ctx.save();
      this.particles.forEach((particle) => {
        const alpha = .08 + (1 - Math.abs(particle.life - .6)) * .27;
        ctx.beginPath();
        ctx.moveTo(particle.oldX, particle.oldY);
        ctx.lineTo(particle.x, particle.y);
        ctx.strokeStyle = `rgba(105,245,204,${alpha})`;
        ctx.lineWidth = particle.speed * .7;
        ctx.stroke();
      });
      for (let row = 0; row < 5; row += 1) {
        const y = lerp(area.top + 25, area.bottom - 25, row / 4);
        ctx.beginPath();
        for (let step = 0; step <= 40; step += 1) {
          const x = lerp(area.left, area.right, step / 40);
          const dx = x - this.forceSource.x;
          const dy = y - this.forceSource.y;
          const distance = Math.hypot(dx, dy) || 1;
          const bend = clamp(1 - distance / 220, 0, 1) * 42 * Math.sign(dy || 1);
          const py = y + bend * Math.sin(step / 40 * Math.PI);
          step === 0 ? ctx.moveTo(x, py) : ctx.lineTo(x, py);
        }
        ctx.strokeStyle = row === 2 ? 'rgba(182,255,231,.42)' : 'rgba(101,245,201,.16)';
        ctx.lineWidth = row === 2 ? 1.4 : .8;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(this.forceSource.x, this.forceSource.y, 24, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(255,198,111,.68)';
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(this.forceSource.x, this.forceSource.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = '#ffc66f';
      ctx.shadowColor = '#ffc66f';
      ctx.shadowBlur = 18;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.fillStyle = 'rgba(255,198,111,.8)';
      ctx.font = '700 10px Inter, sans-serif';
      ctx.fillText('FORCE', this.forceSource.x + 34, this.forceSource.y + 4);
      ctx.restore();
    }

    draw() {
      this.clear();
      if (this.mode === 'cloth') this.drawCloth();
      if (this.mode === 'rod') this.drawRod();
      if (this.mode === 'soft') this.drawSoft();
      if (this.mode === 'collider') this.drawCollider();
      if (this.mode === 'force') this.drawForce();
    }

    frame(time) {
      requestAnimationFrame((next) => this.frame(next));
      if (!this.visible || document.hidden) return;
      this.update(time);
      this.draw();
    }
  }

  const roleCanvas = document.querySelector('#role-canvas');
  const roleSimulation = roleCanvas && !reducedMotion ? new RoleSimulation(roleCanvas) : null;
  const roleLab = document.querySelector('[data-role-lab]');
  if (roleLab) {
    const stage = roleLab.querySelector('[data-role-stage]');
    const buttons = [...roleLab.querySelectorAll('[data-role]')];
    const overline = roleLab.querySelector('[data-role-overline]');
    const title = roleLab.querySelector('[data-role-title]');
    const description = roleLab.querySelector('[data-role-description]');
    const points = roleLab.querySelector('[data-role-points]');
    const instruction = roleLab.querySelector('[data-role-instruction]');
    const activate = (mode) => {
      const data = roleData[mode];
      if (!data) return;
      buttons.forEach((button) => {
        const active = button.dataset.role === mode;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', `${active}`);
      });
      stage?.classList.remove('role-changing');
      void stage?.offsetWidth;
      stage?.classList.add('role-changing');
      if (overline) overline.textContent = data.overline;
      if (title) title.textContent = data.title;
      if (description) description.textContent = data.description;
      if (points) points.innerHTML = data.points.map((point) => `<li>${point}</li>`).join('');
      if (instruction) instruction.textContent = data.instruction;
      roleSimulation?.setMode(mode);
    };
    buttons.forEach((button) => button.addEventListener('click', () => activate(button.dataset.role)));
  }

  class WorkflowSimulation extends CanvasHost {
    constructor(canvas) {
      super(canvas);
      this.step = 0;
      this.time = 0;
      requestAnimationFrame((time) => this.frame(time));
    }
    setStep(step) { this.step = step; }
    drawMesh() {
      const ctx = this.ctx;
      const cx = this.width * .53;
      const cy = this.height * .46;
      ctx.save();
      ctx.strokeStyle = 'rgba(101,245,201,.24)';
      ctx.lineWidth = .8;
      for (let row = 0; row < 13; row += 1) {
        ctx.beginPath();
        for (let col = 0; col < 15; col += 1) {
          const u = col / 14;
          const v = row / 12;
          const x = cx - 125 + u * 250 + Math.sin(v * 6 + this.time) * 8;
          const y = cy - 120 + v * 240 + Math.sin(u * 8 - this.time * 1.3) * 8;
          col === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.ellipse(cx, cy + 20, 62, 105, 0, 0, Math.PI * 2);
      ctx.setLineDash([6, 7]);
      ctx.strokeStyle = 'rgba(101,214,255,.36)';
      ctx.stroke();
      ctx.restore();
    }
    drawMaterials() {
      const ctx = this.ctx;
      const cx = this.width * .53;
      const cy = this.height * .48;
      const labels = ['Silk', 'Denim', 'Linen', 'Leather', 'Jersey'];
      labels.forEach((label, index) => {
        const y = cy - 105 + index * 48;
        ctx.save();
        ctx.translate(cx, y);
        ctx.rotate((index - 2) * .018);
        ctx.fillStyle = index === 2 ? 'rgba(101,245,201,.15)' : 'rgba(255,255,255,.025)';
        ctx.strokeStyle = index === 2 ? 'rgba(101,245,201,.45)' : 'rgba(255,255,255,.09)';
        ctx.beginPath();
        ctx.roundRect(-120, -18, 240, 36, 8);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = index === 2 ? '#dffff5' : '#7f8d88';
        ctx.font = '700 11px Inter, sans-serif';
        ctx.fillText(label, -100, 4);
        ctx.restore();
      });
    }
    drawChecks() {
      const ctx = this.ctx;
      const items = ['Geometry ready', 'Cache writable', 'Solver matched', 'VRAM checked'];
      const startX = this.width * .35;
      const startY = this.height * .31;
      items.forEach((item, index) => {
        const y = startY + index * 56;
        ctx.beginPath();
        ctx.arc(startX, y, 10, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(101,245,201,.15)';
        ctx.fill();
        ctx.strokeStyle = '#65f5c9';
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(startX - 4, y);
        ctx.lineTo(startX - 1, y + 4);
        ctx.lineTo(startX + 5, y - 5);
        ctx.strokeStyle = '#b6ffe7';
        ctx.lineWidth = 1.6;
        ctx.stroke();
        ctx.fillStyle = '#9eaaa6';
        ctx.font = '650 12px Inter, sans-serif';
        ctx.fillText(item, startX + 24, y + 4);
      });
    }
    drawTelemetry() {
      const ctx = this.ctx;
      const left = this.width * .22;
      const right = this.width * .82;
      const top = this.height * .27;
      const bottom = this.height * .68;
      ctx.strokeStyle = 'rgba(101,245,201,.07)';
      for (let row = 0; row < 7; row += 1) {
        const y = lerp(top, bottom, row / 6);
        ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
      }
      for (let col = 0; col < 9; col += 1) {
        const x = lerp(left, right, col / 8);
        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
      }
      ctx.beginPath();
      for (let step = 0; step <= 60; step += 1) {
        const x = lerp(left, right, step / 60);
        const y = lerp(top + 35, bottom - 25, .48 + Math.sin(step * .38 + this.time * 2) * .12 + Math.sin(step * .12) * .16);
        step === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.strokeStyle = '#65f5c9';
      ctx.shadowColor = '#65f5c9';
      ctx.shadowBlur = 12;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.fillStyle = '#e8fff7';
      ctx.font = '600 34px Inter, sans-serif';
      ctx.fillText('68%', right - 70, top + 38);
    }
    drawRecovery() {
      const ctx = this.ctx;
      const x = this.width * .31;
      const y = this.height * .31;
      const w = this.width * .43;
      const h = 150;
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, 14);
      ctx.fillStyle = 'rgba(6,13,11,.78)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(101,245,201,.28)';
      ctx.stroke();
      ctx.fillStyle = '#b6ffe7';
      ctx.font = '500 44px Inter, sans-serif';
      ctx.fillText('084', x + 22, y + 58);
      ctx.fillStyle = '#6f817b';
      ctx.font = '750 9px Inter, sans-serif';
      ctx.fillText('CHECKPOINT VERIFIED', x + 22, y + 86);
      ctx.fillStyle = 'rgba(255,255,255,.06)';
      ctx.fillRect(x + 22, y + 104, w - 44, 4);
      ctx.fillStyle = '#65f5c9';
      ctx.fillRect(x + 22, y + 104, (w - 44) * .72, 4);
      ctx.fillStyle = '#65f5c9';
      ctx.font = '650 10px Inter, sans-serif';
      ctx.fillText('RESUME FROM LATEST  →', x + 22, y + 134);
    }
    draw(time) {
      this.clear();
      this.time = time * .001;
      if (this.step === 0) this.drawMesh();
      if (this.step === 1) this.drawMaterials();
      if (this.step === 2) this.drawChecks();
      if (this.step === 3) this.drawTelemetry();
      if (this.step === 4) this.drawRecovery();
    }
    frame(time) {
      requestAnimationFrame((next) => this.frame(next));
      if (!this.visible || document.hidden) return;
      this.draw(time);
    }
  }

  const workflowCanvas = document.querySelector('#workflow-canvas');
  const workflowSimulation = workflowCanvas && !reducedMotion ? new WorkflowSimulation(workflowCanvas) : null;
  const workflowSteps = [...document.querySelectorAll('.workflow-step')];
  const workflowNumber = document.querySelector('[data-workflow-number]');
  const workflowTitle = document.querySelector('[data-workflow-title]');
  const workflowState = document.querySelector('[data-workflow-state]');
  const workflowVisual = document.querySelector('[data-workflow-visual]');
  const workflowStates = ['OBJECT READY', 'MATERIAL LOADED', 'SCENE VALIDATED', 'SOLVER ACTIVE', 'CHECKPOINT VERIFIED'];
  const setWorkflowStep = (index) => {
    const step = workflowSteps[index];
    if (!step) return;
    workflowSteps.forEach((node, nodeIndex) => node.classList.toggle('active', nodeIndex === index));
    if (workflowNumber) workflowNumber.textContent = String(index + 1).padStart(2, '0');
    if (workflowTitle) workflowTitle.textContent = step.querySelector('h3')?.textContent || '';
    if (workflowState) workflowState.textContent = workflowStates[index] || '';
    workflowVisual?.setAttribute('data-visual-step', `${index}`);
    workflowSimulation?.setStep(index);
  };
  workflowSteps.forEach((step, index) => step.addEventListener('click', () => setWorkflowStep(index)));
  if (!reducedMotion && 'IntersectionObserver' in window && workflowSteps.length) {
    const workflowObserver = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visible) setWorkflowStep(Number(visible.target.dataset.step));
    }, { threshold: [.42, .68], rootMargin: '-17% 0px -35% 0px' });
    workflowSteps.forEach((step) => workflowObserver.observe(step));
  }

  class ThreadField extends CanvasHost {
    constructor(canvas) {
      super(canvas);
      this.pointer = { x: .5, y: .5, targetX: .5, targetY: .5 };
      this.canvas?.parentElement?.addEventListener('pointermove', (event) => {
        const rect = this.canvas.getBoundingClientRect();
        this.pointer.targetX = clamp((event.clientX - rect.left) / rect.width, 0, 1);
        this.pointer.targetY = clamp((event.clientY - rect.top) / rect.height, 0, 1);
      }, { passive: true });
      requestAnimationFrame((time) => this.frame(time));
    }
    draw(time) {
      this.clear();
      const ctx = this.ctx;
      this.pointer.x = lerp(this.pointer.x, this.pointer.targetX, .03);
      this.pointer.y = lerp(this.pointer.y, this.pointer.targetY, .03);
      const rows = 17;
      const columns = 48;
      const phase = time * .00042;
      for (let row = 0; row < rows; row += 1) {
        ctx.beginPath();
        for (let col = 0; col < columns; col += 1) {
          const u = col / (columns - 1);
          const v = row / (rows - 1);
          const dx = u - this.pointer.x;
          const dy = v - this.pointer.y;
          const influence = Math.max(0, 1 - Math.hypot(dx, dy) * 2.5);
          const x = u * this.width + dx * influence * 58;
          const y = v * this.height + Math.sin(u * 8 + phase * 3 + row * .2) * 12 + dy * influence * 44;
          col === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.strokeStyle = `rgba(101,245,201,${.025 + row / rows * .065})`;
        ctx.lineWidth = row % 4 === 0 ? 1 : .6;
        ctx.stroke();
      }
    }
    frame(time) {
      requestAnimationFrame((next) => this.frame(next));
      if (!this.visible || document.hidden) return;
      this.draw(time);
    }
  }

  const ctaCanvas = document.querySelector('#cta-canvas');
  if (ctaCanvas && !reducedMotion) new ThreadField(ctaCanvas);
})();
