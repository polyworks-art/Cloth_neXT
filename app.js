(() => {
  'use strict';

  const root = document.documentElement;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

  document.querySelectorAll('[data-year]').forEach((node) => {
    node.textContent = new Date().getFullYear();
  });

  const header = document.querySelector('[data-header]');
  const progressBar = document.querySelector('.scroll-progress span');
  const updateScroll = () => {
    const scrollTop = window.scrollY || document.documentElement.scrollTop;
    const maxScroll = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
    root.style.setProperty('--scroll-progress', `${clamp(scrollTop / maxScroll, 0, 1)}`);
    if (progressBar) progressBar.style.transform = `scaleX(${clamp(scrollTop / maxScroll, 0, 1)})`;
    header?.classList.toggle('scrolled', scrollTop > 24);
  };
  updateScroll();
  window.addEventListener('scroll', updateScroll, { passive: true });

  const glow = document.querySelector('.cursor-glow');
  if (glow && !reducedMotion && window.matchMedia('(pointer:fine)').matches) {
    window.addEventListener('pointermove', (event) => {
      glow.style.setProperty('--x', `${event.clientX}px`);
      glow.style.setProperty('--y', `${event.clientY}px`);
      glow.classList.add('visible');
    }, { passive: true });
  }

  const revealNodes = document.querySelectorAll('.reveal');
  if (reducedMotion || !('IntersectionObserver' in window)) {
    revealNodes.forEach((node) => node.classList.add('revealed'));
  } else {
    const revealObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('revealed');
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -7% 0px' });
    revealNodes.forEach((node) => revealObserver.observe(node));
  }

  document.querySelectorAll('.magnetic').forEach((element) => {
    if (reducedMotion || !window.matchMedia('(pointer:fine)').matches) return;
    element.addEventListener('pointermove', (event) => {
      const rect = element.getBoundingClientRect();
      const x = event.clientX - rect.left - rect.width / 2;
      const y = event.clientY - rect.top - rect.height / 2;
      element.style.transform = `translate(${x * 0.08}px, ${y * 0.12}px)`;
    });
    element.addEventListener('pointerleave', () => {
      element.style.transform = '';
    });
  });

  const parallaxStage = document.querySelector('[data-parallax-stage]');
  if (parallaxStage && !reducedMotion && window.matchMedia('(pointer:fine)').matches) {
    parallaxStage.addEventListener('pointermove', (event) => {
      const rect = parallaxStage.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      parallaxStage.style.setProperty('--parallax-x', `${x}`);
      parallaxStage.style.setProperty('--parallax-y', `${y}`);
    });
    parallaxStage.addEventListener('pointerleave', () => {
      parallaxStage.style.setProperty('--parallax-x', '0');
      parallaxStage.style.setProperty('--parallax-y', '0');
    });
  }

  document.querySelectorAll('.tilt-card').forEach((card) => {
    if (reducedMotion || !window.matchMedia('(pointer:fine)').matches) return;
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

  const roleData = {
    cloth: {
      overline: 'LAYERED CONTACT',
      title: 'Garments that can actually meet.',
      description: 'Simulate multiple cloth objects together with self contact, cloth-to-cloth interaction, animated pins, friction regions and focused material controls.',
      points: ['Shared multi-object solve', 'Static and Follow Animation pins', 'Per-object contact controls']
    },
    rod: {
      overline: 'CURVE DYNAMICS',
      title: 'Cables, cords and flexible lines.',
      description: 'Bring Rod and Cable curves into the same artist-focused system for ropes, straps, wires and other thin flexible structures.',
      points: ['Curve-based setup', 'Unified collision workflow', 'Shared bake monitoring']
    },
    soft: {
      overline: 'VOLUMETRIC RESPONSE',
      title: 'Soft volume without a second workflow.',
      description: 'Use closed manifold meshes as deformable soft bodies. Cloth NeXt prepares the internal tetrahedral volume automatically.',
      points: ['Automatic volume preparation', 'Collision-aware deformation', 'Same validation and cache pipeline']
    },
    collider: {
      overline: 'ANIMATED CONTACT',
      title: 'Collision geometry that keeps moving.',
      description: 'Use static, rigid animated or topology-preserving deforming colliders with motion sampling for fast and curved animation.',
      points: ['Static and animated modes', 'Deforming character colliders', 'Adjustable motion samples']
    },
    force: {
      overline: 'KEYFRAME-READY FORCES',
      title: 'Direct the motion, not just gravity.',
      description: 'Drive supported gravity, wind and air behavior from Blender objects so environmental motion stays visible and animatable.',
      points: ['Gravity and wind controls', 'Air density and friction', 'Vertex air damping']
    }
  };

  const roleExplorer = document.querySelector('[data-role-explorer]');
  if (roleExplorer) {
    const display = roleExplorer.querySelector('.role-display');
    const overline = roleExplorer.querySelector('[data-role-overline]');
    const title = roleExplorer.querySelector('[data-role-title]');
    const description = roleExplorer.querySelector('[data-role-description]');
    const points = roleExplorer.querySelector('[data-role-points]');
    const buttons = [...roleExplorer.querySelectorAll('[data-role]')];

    const activateRole = (role) => {
      const content = roleData[role];
      if (!content || !display) return;
      buttons.forEach((button) => {
        const active = button.dataset.role === role;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', `${active}`);
      });
      display.dataset.activeRole = role;
      display.classList.remove('role-changing');
      void display.offsetWidth;
      display.classList.add('role-changing');
      overline.textContent = content.overline;
      title.textContent = content.title;
      description.textContent = content.description;
      points.innerHTML = content.points.map((point) => `<li>${point}</li>`).join('');
    };

    buttons.forEach((button) => button.addEventListener('click', () => activateRole(button.dataset.role)));
  }

  const storySteps = [...document.querySelectorAll('.story-step')];
  const storyNumber = document.querySelector('[data-story-number]');
  const storyTitle = document.querySelector('[data-story-title]');
  const storyStatus = document.querySelector('[data-story-status]');
  const storyStatuses = ['READY FOR SETUP', 'MATERIAL LOADED', 'SCENE VALIDATED', 'SOLVER RUNNING', 'CACHE VERIFIED'];

  const setStoryStep = (index) => {
    const step = storySteps[index];
    if (!step) return;
    storySteps.forEach((node, nodeIndex) => node.classList.toggle('active', nodeIndex === index));
    if (storyNumber) storyNumber.textContent = String(index + 1).padStart(2, '0');
    if (storyTitle) storyTitle.textContent = step.querySelector('h3')?.textContent || '';
    if (storyStatus) storyStatus.textContent = storyStatuses[index] || '';
    document.querySelector('[data-story-visual]')?.setAttribute('data-visual-step', `${index}`);
  };

  storySteps.forEach((step, index) => {
    step.addEventListener('click', () => setStoryStep(index));
  });

  if (!reducedMotion && 'IntersectionObserver' in window && storySteps.length) {
    const stepObserver = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visible) setStoryStep(Number(visible.target.dataset.step));
    }, { threshold: [0.45, 0.7], rootMargin: '-16% 0px -34% 0px' });
    storySteps.forEach((step) => stepObserver.observe(step));
  }

  const frameReadout = document.querySelector('[data-frame]');
  if (frameReadout && !reducedMotion) {
    let frame = 84;
    window.setInterval(() => {
      frame = frame >= 160 ? 84 : frame + 1;
      frameReadout.textContent = String(frame).padStart(3, '0');
    }, 220);
  }

  function createMeshAnimation(canvas, options = {}) {
    if (!canvas) return;
    const context = canvas.getContext('2d', { alpha: true });
    if (!context) return;

    let width = 0;
    let height = 0;
    let dpr = 1;
    let pointerX = 0.5;
    let pointerY = 0.45;
    let targetX = 0.5;
    let targetY = 0.45;
    let visible = true;
    const columns = options.columns || 24;
    const rows = options.rows || 18;
    const speed = options.speed || 0.00065;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      width = Math.max(rect.width, 1);
      height = Math.max(rect.height, 1);
      dpr = Math.min(window.devicePixelRatio || 1, 1.75);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener('resize', resize, { passive: true });

    const host = canvas.parentElement || canvas;
    host.addEventListener('pointermove', (event) => {
      const rect = canvas.getBoundingClientRect();
      targetX = clamp((event.clientX - rect.left) / rect.width, 0, 1);
      targetY = clamp((event.clientY - rect.top) / rect.height, 0, 1);
    }, { passive: true });
    host.addEventListener('pointerleave', () => {
      targetX = 0.5;
      targetY = 0.45;
    });

    if ('IntersectionObserver' in window) {
      new IntersectionObserver((entries) => {
        visible = entries[0]?.isIntersecting ?? true;
      }, { rootMargin: '200px' }).observe(canvas);
    }

    const draw = (time) => {
      requestAnimationFrame(draw);
      if (!visible || document.hidden) return;
      pointerX += (targetX - pointerX) * 0.035;
      pointerY += (targetY - pointerY) * 0.035;
      context.clearRect(0, 0, width, height);

      const points = [];
      const marginX = width * 0.08;
      const marginY = height * 0.08;
      const fieldWidth = width - marginX * 2;
      const fieldHeight = height - marginY * 2;
      const phase = time * speed;

      for (let row = 0; row < rows; row += 1) {
        const rowPoints = [];
        for (let column = 0; column < columns; column += 1) {
          const u = column / (columns - 1);
          const v = row / (rows - 1);
          const dx = u - pointerX;
          const dy = v - pointerY;
          const distance = Math.sqrt(dx * dx + dy * dy);
          const influence = Math.max(0, 1 - distance * 2.7);
          const wave = Math.sin(u * 8.4 + phase * 2.2) * 9 + Math.cos(v * 7.2 - phase * 1.6) * 7;
          const fold = Math.sin((u + v) * 11 - phase * 2.8) * 5;
          const push = influence * (options.pointerStrength || 34);
          const x = marginX + u * fieldWidth + Math.sin(v * 6 + phase) * 7 + dx * push;
          const y = marginY + v * fieldHeight + wave + fold + dy * push;
          rowPoints.push({ x, y, influence });
        }
        points.push(rowPoints);
      }

      context.lineWidth = 0.75;
      for (let row = 0; row < rows; row += 1) {
        context.beginPath();
        points[row].forEach((point, index) => index === 0 ? context.moveTo(point.x, point.y) : context.lineTo(point.x, point.y));
        context.strokeStyle = `rgba(105, 255, 211, ${0.05 + row / rows * 0.08})`;
        context.stroke();
      }
      for (let column = 0; column < columns; column += 1) {
        context.beginPath();
        for (let row = 0; row < rows; row += 1) {
          const point = points[row][column];
          row === 0 ? context.moveTo(point.x, point.y) : context.lineTo(point.x, point.y);
        }
        context.strokeStyle = `rgba(105, 255, 211, ${0.04 + column / columns * 0.055})`;
        context.stroke();
      }

      points.forEach((row) => row.forEach((point) => {
        const alpha = 0.12 + point.influence * 0.55;
        context.beginPath();
        context.arc(point.x, point.y, point.influence > 0.25 ? 1.4 : 0.7, 0, Math.PI * 2);
        context.fillStyle = `rgba(132, 255, 222, ${alpha})`;
        context.fill();
      }));
    };

    requestAnimationFrame(draw);
  }

  if (!reducedMotion) {
    createMeshAnimation(document.querySelector('#cloth-canvas'), { columns: 25, rows: 19, pointerStrength: 32, speed: 0.00072 });
    createMeshAnimation(document.querySelector('#cta-canvas'), { columns: 31, rows: 13, pointerStrength: 18, speed: 0.00042 });
  }
})();
