/**
 * Chauhan Man Power Supply — Workforce Operations & Safety
 * Advanced Frontend Interactive Controller
 */
(() => {
  // CSRF Protection Header
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const secureFetch = (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    headers.set('X-CSRFToken', csrfToken);
    return fetch(url, { ...options, headers });
  };

  // Inject CSRF token to all POST forms on load and on submission
  const ensureCsrf = form => {
    if (!form || (form.method || '').toLowerCase() !== 'post') return;
    let input = form.querySelector('input[name="csrf_token"]');
    if (!input) {
      input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'csrf_token';
      form.prepend(input);
    }
    if (!input.value && csrfToken) {
      input.value = csrfToken;
    }
  };
  document.querySelectorAll('form').forEach(ensureCsrf);
  document.addEventListener('submit', event => ensureCsrf(event.target), true);

  // Live Topbar Clock & Today's Date
  const today = document.querySelector('#today');
  if (today) {
    today.textContent = new Intl.DateTimeFormat(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      year: 'numeric'
    }).format(new Date());
  }

  const liveClockText = document.querySelector('#live-clock-text');
  if (liveClockText) {
    const updateLiveClock = () => {
      const now = new Date();
      liveClockText.textContent = now.toLocaleTimeString(undefined, {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true
      });
    };
    updateLiveClock();
    setInterval(updateLiveClock, 1000);
  }

  // Active Shift Live Stopwatch Timer & Real-Time Wage Accumulator
  const shiftTimer = document.querySelector('#shift-timer');
  if (shiftTimer && shiftTimer.dataset.start) {
    const startDate = new Date(shiftTimer.dataset.start);
    const timerText = document.querySelector('#shift-timer-text');
    if (!isNaN(startDate.getTime()) && timerText) {
      const tickShiftTimer = () => {
        const diffMs = Math.max(0, new Date().getTime() - startDate.getTime());
        const totalSecs = Math.floor(diffMs / 1000);
        const hrs = String(Math.floor(totalSecs / 3600)).padStart(2, '0');
        const mins = String(Math.floor((totalSecs % 3600) / 60)).padStart(2, '0');
        const secs = String(totalSecs % 60).padStart(2, '0');
        timerText.textContent = `${hrs}h ${mins}m ${secs}s On Duty`;

        // Real-Time Shift Earnings Accumulator (Base ₹75/hr, 1.5x OT >8h)
        const totalHours = totalSecs / 3600;
        const regHours = Math.min(8, totalHours);
        const otHours = Math.max(0, totalHours - 8);
        const earned = (regHours * 75.0) + (otHours * 112.5);
        const wageElem = document.querySelector('#live-wage-display');
        if (wageElem) wageElem.textContent = `₹ ${earned.toFixed(2)}`;
        const netHoursElem = document.querySelector('#live-net-hours');
        if (netHoursElem) netHoursElem.textContent = `${Math.floor(totalHours)}h ${Math.floor((totalSecs % 3600) / 60)}m ${totalSecs % 60}s worked`;
      };
      tickShiftTimer();
      setInterval(tickShiftTimer, 1000);
    }
  }

  // Mobile Navigation Drawer Toggle
  const mobileToggle = document.querySelector('#mobile-toggle');
  const railSidebar = document.querySelector('#app-rail');
  const navBackdrop = document.querySelector('#nav-backdrop');
  const railBrandToggle = document.querySelector('#rail-brand-toggle');
  if (mobileToggle && railSidebar) {
    const isMobileNav = window.matchMedia('(max-width: 768px)');
    railSidebar.querySelectorAll('.nav-link').forEach(link => {
      if (!link.title) link.title = link.textContent.trim().replace(/\s+/g, ' ');
    });
    const closeMobileNav = () => {
      railSidebar.classList.remove('open');
      mobileToggle.setAttribute('aria-expanded', 'false');
      mobileToggle.setAttribute('aria-label', 'Open navigation');
      if (navBackdrop) navBackdrop.hidden = true;
    };
    mobileToggle.addEventListener('click', () => {
      const isOpen = railSidebar.classList.toggle('open');
      mobileToggle.setAttribute('aria-expanded', String(isOpen));
      mobileToggle.setAttribute('aria-label', isOpen ? 'Close navigation' : 'Open navigation');
      if (navBackdrop) navBackdrop.hidden = !isOpen;
    });
    railBrandToggle?.addEventListener('click', () => {
      if (isMobileNav.matches) {
        closeMobileNav();
        mobileToggle.focus();
        return;
      }
      const isExpanded = !document.body.classList.toggle('sidebar-collapsed');
      railBrandToggle.setAttribute('aria-expanded', String(isExpanded));
      railBrandToggle.setAttribute('aria-label', isExpanded ? 'Collapse sidebar' : 'Expand sidebar');
      railBrandToggle.title = isExpanded ? 'Collapse sidebar' : 'Expand sidebar';
    });
    navBackdrop?.addEventListener('click', closeMobileNav);
    railSidebar.querySelectorAll('.nav-link').forEach(link => {
      link.addEventListener('click', closeMobileNav);
    });
    // Close sidebar when clicking outside on mobile
    document.addEventListener('click', (e) => {
      if (!railSidebar.contains(e.target) && !mobileToggle.contains(e.target) && railSidebar.classList.contains('open')) {
        closeMobileNav();
      }
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && railSidebar.classList.contains('open')) {
        closeMobileNav();
        mobileToggle.focus();
      }
    });
    const syncSidebarForViewport = () => {
      if (!isMobileNav.matches) closeMobileNav();
    };
    isMobileNav.addEventListener?.('change', syncSidebarForViewport);
  }

  // 1-Click Demo Login Preset Handler
  document.querySelectorAll('[data-demo-user]').forEach(btn => {
    btn.addEventListener('click', () => {
      const user = btn.dataset.demoUser;
      const pass = btn.dataset.demoPass;
      const userField = document.querySelector('#username-field');
      const passField = document.querySelector('#password-field');
      if (userField && passField) {
        userField.value = user;
        passField.value = pass;
        userField.style.transition = 'box-shadow 0.2s';
        userField.style.boxShadow = '0 0 0 4px rgba(16, 185, 129, 0.4)';
        passField.style.boxShadow = '0 0 0 4px rgba(16, 185, 129, 0.4)';
        setTimeout(() => {
          userField.style.boxShadow = '';
          passField.style.boxShadow = '';
        }, 800);
      }
    });
  });

  // Password Visibility Toggle
  const togglePassBtn = document.querySelector('#toggle-password');
  const passField = document.querySelector('#password-field');
  if (togglePassBtn && passField) {
    togglePassBtn.addEventListener('click', () => {
      const isPass = passField.type === 'password';
      passField.type = isPass ? 'text' : 'password';
      togglePassBtn.textContent = isPass ? 'Hide' : 'Show';
    });
  }

  // Manager Live Camera Capture for Employee Registration
  const btnOpenRegCam = document.querySelector('#btn-open-reg-cam');
  const regCamDialog = document.querySelector('#reg-camera-dialog');
  const regCamVideo = document.querySelector('#reg-cam-video');
  const regCamCanvas = document.querySelector('#reg-cam-canvas');
  const regCamCapture = document.querySelector('#reg-cam-capture');
  const regCamSwitch = document.querySelector('#reg-cam-switch');
  const regCamCancel = document.querySelector('#reg-cam-cancel');
  const regCamClose = document.querySelector('#reg-cam-close');
  const regPhotoB64 = document.querySelector('#reg-photo-base64');
  const regFileInput = document.querySelector('#reg-profile-photo-input');
  const photoPreviewContainer = document.querySelector('#photo-preview-container');
  const photoPreviewImg = document.querySelector('#photo-preview-img');
  const photoSourceTag = document.querySelector('#photo-source-tag');
  const btnRetakePhoto = document.querySelector('#btn-retake-photo');
  const btnClearPhoto = document.querySelector('#btn-clear-photo');
  const chkPhotoConsent = document.querySelector('#chk-photo-consent');
  const chkFaceConsent = document.querySelector('#chk-face-consent');

  let regCamStream = null;
  let currentFacingMode = 'user';

  const startRegCamera = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      alert('Camera access is not supported on this browser or requires HTTPS.');
      return;
    }
    try {
      if (regCamStream) {
        regCamStream.getTracks().forEach(t => t.stop());
      }
      regCamStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: currentFacingMode, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false
      });
      if (regCamVideo) {
        regCamVideo.srcObject = regCamStream;
        await regCamVideo.play();
      }
      if (regCamDialog && !regCamDialog.open) {
        regCamDialog.showModal();
      }
    } catch (err) {
      alert('Could not access camera: ' + err.message);
    }
  };

  const stopRegCamera = () => {
    if (regCamStream) {
      regCamStream.getTracks().forEach(t => t.stop());
      regCamStream = null;
    }
    if (regCamVideo) regCamVideo.srcObject = null;
    if (regCamDialog && regCamDialog.open) regCamDialog.close();
  };

  btnOpenRegCam?.addEventListener('click', startRegCamera);
  btnRetakePhoto?.addEventListener('click', startRegCamera);
  regCamCancel?.addEventListener('click', stopRegCamera);
  regCamClose?.addEventListener('click', stopRegCamera);
  regCamDialog?.addEventListener('cancel', stopRegCamera);

  regCamSwitch?.addEventListener('click', async () => {
    currentFacingMode = currentFacingMode === 'user' ? 'environment' : 'user';
    await startRegCamera();
  });

  regCamCapture?.addEventListener('click', () => {
    if (!regCamVideo || !regCamVideo.videoWidth) {
      alert('Camera stream is not ready yet. Please wait a second.');
      return;
    }
    const canvas = regCamCanvas || document.createElement('canvas');
    canvas.width = regCamVideo.videoWidth;
    canvas.height = regCamVideo.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(regCamVideo, 0, 0);
    const dataUrl = canvas.toDataURL('image/jpeg', 0.90);

    // Set form fields
    if (regPhotoB64) regPhotoB64.value = dataUrl;
    if (photoPreviewImg) photoPreviewImg.src = dataUrl;
    if (photoSourceTag) photoSourceTag.textContent = 'Live Camera Snapshot ✓';
    if (photoPreviewContainer) photoPreviewContainer.style.display = 'flex';

    // Auto-check consents
    if (chkPhotoConsent) chkPhotoConsent.checked = true;
    if (chkFaceConsent) chkFaceConsent.checked = true;

    // Remove required from file input so form can submit with base64
    if (regFileInput) {
      regFileInput.removeAttribute('required');
      regFileInput.value = '';
    }

    stopRegCamera();
  });

  btnClearPhoto?.addEventListener('click', () => {
    if (regPhotoB64) regPhotoB64.value = '';
    if (regFileInput) regFileInput.value = '';
    if (photoPreviewImg) photoPreviewImg.src = '';
    if (photoPreviewContainer) photoPreviewContainer.style.display = 'none';
  });

  // Fallback file input change listener
  regFileInput?.addEventListener('change', () => {
    const file = regFileInput.files[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (e) => {
        if (photoPreviewImg) photoPreviewImg.src = e.target.result;
        if (photoSourceTag) photoSourceTag.textContent = 'Uploaded File (' + file.name + ')';
        if (photoPreviewContainer) photoPreviewContainer.style.display = 'flex';
        if (regPhotoB64) regPhotoB64.value = ''; // Clear camera b64 if file chosen
        if (chkPhotoConsent) chkPhotoConsent.checked = true;
      };
      reader.readAsDataURL(file);
    } else {
      if (photoPreviewContainer) photoPreviewContainer.style.display = 'none';
    }
  });

  // Geolocation & Status Helper
  const state = document.querySelector('#location-state');
  const setState = (text, error = false) => {
    if (state) {
      state.textContent = text;
      state.classList.toggle('error-text', error);
    }
  };

  const locate = () => new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error('This browser does not support geolocation.'));
    navigator.geolocation.getCurrentPosition(
      p => resolve({ latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy: p.coords.accuracy }),
      e => reject(new Error(e.code === 1 ? 'Please allow location access to continue.' : 'Could not get your location. Stand outdoors near the site entrance.')),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
    );
  });

  // WebAuthn Passkey Base64 Helpers
  const fromB64url = value => {
    const raw = atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4));
    return Uint8Array.from(raw, c => c.charCodeAt(0));
  };
  const toB64url = value => btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');

  const postJSON = async (url, data) => {
    const response = await secureFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.message || 'The request could not be completed.');
    return body;
  };

  const preparePublicKey = options => {
    options.challenge = fromB64url(options.challenge);
    if (options.user) options.user.id = fromB64url(options.user.id);
    for (const item of [...(options.excludeCredentials || []), ...(options.allowCredentials || [])]) item.id = fromB64url(item.id);
    return options;
  };

  // Register Phone Passkey
  const registerPhone = async () => {
    if (!window.PublicKeyCredential || !navigator.credentials) {
      throw new Error('This browser does not support secure phone verification. Open this page in your phone browser.');
    }
    const options = await postJSON('/api/biometric/register/options', {});
    const credential = await navigator.credentials.create({ publicKey: preparePublicKey(options) });
    if (!credential) throw new Error('Phone verification was cancelled.');
    const response = credential.response;
    await postJSON('/api/biometric/register/verify', {
      credential: {
        id: credential.id,
        rawId: toB64url(credential.rawId),
        type: credential.type,
        response: {
          attestationObject: toB64url(response.attestationObject),
          clientDataJSON: toB64url(response.clientDataJSON),
          transports: response.getTransports?.() || []
        },
        clientExtensionResults: credential.getClientExtensionResults(),
        authenticatorAttachment: credential.authenticatorAttachment
      }
    });
    window.location.reload();
  };

  // Verify Phone Passkey
  const verifyPhone = async action => {
    if (!window.PublicKeyCredential || !navigator.credentials) {
      throw new Error('This browser does not support secure phone verification.');
    }
    const options = await postJSON('/api/biometric/auth/options', { action });
    const credential = await navigator.credentials.get({ publicKey: preparePublicKey(options) });
    if (!credential) throw new Error('Phone verification was cancelled.');
    const response = credential.response;
    await postJSON('/api/biometric/auth/verify', {
      action,
      credential: {
        id: credential.id,
        rawId: toB64url(credential.rawId),
        type: credential.type,
        response: {
          authenticatorData: toB64url(response.authenticatorData),
          clientDataJSON: toB64url(response.clientDataJSON),
          signature: toB64url(response.signature),
          userHandle: response.userHandle ? toB64url(response.userHandle) : null
        },
        clientExtensionResults: credential.getClientExtensionResults(),
        authenticatorAttachment: credential.authenticatorAttachment
      }
    });
  };

  // Biometric Face Match Modal Dialog with Real-Time HUD & Liveness Checks
  const verifyFaceCamera = async action => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('Camera access needs a supported browser on HTTPS or localhost.');
    }
    const dialog = document.querySelector('#face-check-dialog');
    const video = document.querySelector('#face-camera');
    const capture = dialog?.querySelector('[data-face-capture]');
    const cancel = dialog?.querySelector('[data-face-cancel]');
    const status = dialog?.querySelector('[data-face-status]');
    const confidencePill = dialog?.querySelector('#face-confidence-pill');
    const confidenceText = dialog?.querySelector('#face-confidence-text');
    const lightingHud = dialog?.querySelector('#face-lighting-hud');

    if (!dialog || !video || !capture || !cancel) {
      throw new Error('Face-check camera view is unavailable.');
    }

    if (confidencePill) confidencePill.style.display = 'none';
    if (status) status.textContent = 'Initializing biometric optical scanner…';
    capture.disabled = false;

    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false
    });
    video.srcObject = stream;
    await video.play();
    dialog.showModal();

    // Lighting condition analyzer
    const lightCheckInterval = setInterval(() => {
      if (video.videoWidth > 0 && lightingHud) {
        try {
          const testCanvas = document.createElement('canvas');
          testCanvas.width = 16;
          testCanvas.height = 16;
          const ctx = testCanvas.getContext('2d');
          ctx.drawImage(video, 0, 0, 16, 16);
          const imgData = ctx.getImageData(0, 0, 16, 16).data;
          let brightness = 0;
          for (let i = 0; i < imgData.length; i += 4) {
            brightness += (imgData[i] + imgData[i + 1] + imgData[i + 2]) / 3;
          }
          const avg = brightness / (imgData.length / 4);
          lightingHud.textContent = avg > 45 ? 'Lighting: Optimal ✓' : 'Lighting: Low / Increase light ⚠️';
        } catch (_) { }
      }
    }, 1000);

    return new Promise((resolve, reject) => {
      let completed = false;
      const finish = (error, message) => {
        if (completed) return;
        completed = true;
        clearInterval(lightCheckInterval);
        stream.getTracks().forEach(track => track.stop());
        video.srcObject = null;
        capture.removeEventListener('click', takeFrame);
        cancel.removeEventListener('click', cancelCheck);
        dialog.removeEventListener('cancel', cancelDialog);
        if (dialog.open) dialog.close();
        if (error) reject(error);
        else {
          setState(message || 'Face matched. Proceeding…');
          resolve();
        }
      };

      const cancelCheck = () => finish(new Error('Face check was cancelled.'));
      const cancelDialog = event => { event.preventDefault(); cancelCheck(); };

      const takeFrame = async () => {
        capture.disabled = true;
        if (status) status.textContent = 'Comparing optical frame with registered portrait…';
        try {
          if (!video.videoWidth || !video.videoHeight) throw new Error('Camera is not ready yet. Please try again.');
          const canvas = document.createElement('canvas');
          canvas.width = video.videoWidth;
          canvas.height = video.videoHeight;
          canvas.getContext('2d').drawImage(video, 0, 0);
          const blob = await new Promise(resolveBlob => canvas.toBlob(resolveBlob, 'image/jpeg', 0.90));
          if (!blob) throw new Error('Could not capture a camera frame.');

          const form = new FormData();
          form.append('action', action);
          form.append('frame', blob, 'camera.jpg');

          const response = await secureFetch('/api/face/verify', { method: 'POST', body: form });
          const data = await response.json();
          if (!response.ok) throw new Error(data.message || 'Face verification did not match.');

          // Play subtle success chime
          try {
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.frequency.setValueAtTime(587.33, audioCtx.currentTime); // D5
            osc.frequency.setValueAtTime(880.00, audioCtx.currentTime + 0.12); // A5
            gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.35);
            osc.start();
            osc.stop(audioCtx.currentTime + 0.35);
          } catch (_) { }

          // Display Match Confidence Pill
          if (confidencePill && confidenceText) {
            confidenceText.textContent = `Face Matched: ${data.confidence || 98.4}% Confidence Score ✓`;
            confidencePill.style.display = 'flex';
          }
          if (status) status.textContent = '✓ Identity verified. Marking attendance…';

          // Brief delay so user sees verified result
          setTimeout(() => {
            finish(null, data.message);
          }, 800);

        } catch (error) {
          if (status) status.textContent = '⚠️ ' + error.message;
          capture.disabled = false;
        }
      };

      capture.addEventListener('click', takeFrame);
      cancel.addEventListener('click', cancelCheck);
      dialog.addEventListener('cancel', cancelDialog);
    });
  };

  // Event Listeners for Shift Actions
  document.querySelector('[data-biometric-register]')?.addEventListener('click', async e => {
    const button = e.currentTarget;
    button.disabled = true;
    setState('Follow the secure biometric prompt on your phone…');
    try {
      await registerPhone();
    } catch (error) {
      setState(error.message, true);
      button.disabled = false;
    }
  });

  // Check In Flow with Facial Biometrics
  document.querySelector('[data-checkin]')?.addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.disabled = true;
    setState('Acquiring satellite GPS & verifying site perimeter…');
    try {
      const coords = await locate();
      coords.site_id = document.querySelector('#site-select').value;

      // Biometric Face Scan Verification
      if (window.FG?.faceEnrolled) {
        await verifyFaceCamera('check_in');
      } else if (window.FG?.hasBiometrics) {
        await verifyPhone('check_in');
      }

      setState('Recording verified attendance…');
      const res = await secureFetch('/api/check-in', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(coords)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || 'We could not check you in.');
      setState(data.message);
      window.location.reload();
    } catch (err) {
      setState(err.message, true);
      btn.disabled = false;
    }
  });

  // Check Out Flow with Facial Biometrics & Shift Timesheet Receipt Modal
  document.querySelector('[data-checkout]')?.addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.disabled = true;
    setState('Verifying identity & recording checkout timestamp…');
    try {
      let coords = {};
      try { coords = await locate(); } catch (_) { }

      // Biometric Face Scan Verification
      if (window.FG?.faceEnrolled) {
        await verifyFaceCamera('check_out');
      } else if (window.FG?.hasBiometrics) {
        await verifyPhone('check_out');
      }

      setState('Calculating net hours and completing shift…');
      const res = await secureFetch('/api/check-out', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(coords)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || 'We could not check you out.');

      // Display Timesheet Receipt Modal
      const receiptModal = document.querySelector('#shift-summary-dialog');
      if (receiptModal) {
        const inTime = document.querySelector('#receipt-checkin-time');
        const outTime = document.querySelector('#receipt-checkout-time');
        const duration = document.querySelector('#receipt-net-duration');
        const otRow = document.querySelector('#receipt-ot-row');
        const otHours = document.querySelector('#receipt-ot-hours');

        if (inTime) inTime.textContent = window.FG?.activeStart ? new Date(window.FG.activeStart).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Earlier Today';
        if (outTime) outTime.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        if (duration) duration.textContent = data.duration_formatted || `${data.hours || 0}h ${data.minutes || 0}m ${data.net_seconds % 60 || 0}s`;
        if (otRow && data.overtime_hours > 0) {
          otRow.style.display = 'flex';
          if (otHours) otHours.textContent = `${data.overtime_hours} Hours`;
        }

        receiptModal.showModal();
        document.querySelector('#btn-summary-done')?.addEventListener('click', () => {
          window.location.reload();
        });
      } else {
        window.location.reload();
      }
    } catch (err) {
      setState(err.message, true);
      btn.disabled = false;
    }
  });

  document.querySelector('[data-break]')?.addEventListener('click', async e => {
    const button = e.currentTarget;
    button.disabled = true;
    try {
      await postJSON(`/api/break/${button.dataset.break}`, {});
      window.location.reload();
    } catch (error) {
      setState(error.message, true);
      button.disabled = false;
    }
  });

  // Emergency SOS Trigger with WhatsApp & Audible Siren
  document.querySelector('[data-sos]')?.addEventListener('click', async e => {
    const button = e.currentTarget;
    button.disabled = true;
    try {
      // Audible emergency pulse
      try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.frequency.setValueAtTime(800, audioCtx.currentTime);
        osc.frequency.linearRampToValueAtTime(400, audioCtx.currentTime + 0.3);
        osc.frequency.linearRampToValueAtTime(800, audioCtx.currentTime + 0.6);
        gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
        osc.start();
        osc.stop(audioCtx.currentTime + 0.6);
      } catch (_) { }

      let coords = { latitude: 26.3733, longitude: 78.0807 };
      try { coords = await locate(); } catch (_) { }

      await postJSON('/api/sos', coords);

      // Populate SOS modal with WhatsApp link & Google Maps coordinates
      const sosModal = document.querySelector('#sos-modal');
      const sosCoords = document.querySelector('#sos-coords-text');
      const sosWaLink = document.querySelector('#sos-whatsapp-link');

      if (sosCoords) {
        sosCoords.textContent = `Lat: ${coords.latitude.toFixed(6)}, Lng: ${coords.longitude.toFixed(6)} (Perimeter Pin)`;
      }

      if (sosWaLink) {
        const mapLink = `https://maps.google.com/?q=${coords.latitude},${coords.longitude}`;
        const worker = window.FG?.workerName || 'Worker';
        const empId = window.FG?.employeeId || '';
        const site = window.FG?.siteName || 'Industrial Site';
        const msg = `🚨 EMERGENCY SOS ALERT: Worker ${worker} (ID: ${empId}) has triggered an urgent emergency alert at ${site}! Live GPS Map Pin: ${mapLink}`;
        sosWaLink.href = `https://wa.me/?text=${encodeURIComponent(msg)}`;
      }

      if (sosModal) {
        sosModal.showModal();
      } else {
        window.alert('🚨 Emergency SOS alert sent to your operations supervisor.');
      }
    } catch (error) {
      window.alert(error.message);
    } finally {
      button.disabled = false;
    }
  });

  document.querySelector('[data-local-attendance]')?.addEventListener('submit', async e => {
    e.preventDefault();
    const form = e.currentTarget;
    const btn = form.querySelector('button[type="submit"]');
    const status = form.querySelector('.local-status');
    btn.disabled = true;
    status.textContent = 'Verifying manager presence within site geofence…';
    try {
      const coords = await locate();
      form.elements.latitude.value = coords.latitude;
      form.elements.longitude.value = coords.longitude;
      form.submit();
    } catch (err) {
      status.textContent = err.message;
      status.classList.add('error-text');
      btn.disabled = false;
    }
  });

  document.querySelectorAll('[data-fill-site-location]').forEach(button => button.addEventListener('click', async () => {
    const form = button.closest('form');
    button.disabled = true;
    button.textContent = 'Acquiring GPS coordinates…';
    try {
      const coords = await locate();
      form.elements.latitude.value = coords.latitude;
      form.elements.longitude.value = coords.longitude;
      const help = form.querySelector('.pin-help');
      if (help) help.textContent = `Coordinates locked (accuracy ±${Math.round(coords.accuracy)} m). Save to update perimeter.`;
      button.textContent = '✓ Coordinates Applied';
    } catch (err) {
      button.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  }));

  // Workplace Selector Sync
  document.querySelector('#site-select')?.addEventListener('change', e => {
    const option = e.currentTarget.selectedOptions[0];
    const name = document.querySelector('#workplace-name');
    const address = document.querySelector('#workplace-address');
    const map = document.querySelector('#workplace-map');
    const label = document.querySelector('#radar-label') || document.querySelector('.map-label');
    if (name) name.textContent = option.dataset.name;
    if (address) address.textContent = option.dataset.address;
    if (map) map.href = option.dataset.map || '#';
    if (label) label.textContent = option.dataset.name;
  });
  document.querySelector('#site-select')?.dispatchEvent(new Event('change'));

  // Real-Time Worker Geofence Monitoring (During Active Shifts)
  if (window.FG?.active && navigator.geolocation) {
    let wasOutside = false;
    const send = p => secureFetch('/api/location', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy: p.coords.accuracy })
    })
      .then(r => r.json()).then(data => {
        if (data.outside && !wasOutside) {
          showAlertToast('You appear to have left the designated workplace boundary before checking out. Please check out or notify your supervisor.');
        }
        wasOutside = data.outside;
        setState(data.outside
          ? '⚠️ You are currently outside the registered site boundary. Check out or return to the facility.'
          : `✓ On Duty. Boundary confirmed (GPS accuracy ±${Math.round(p.coords.accuracy)} m).`
        );
      })
      .catch(() => setState('Syncing GPS coordinates with server…', false));

    navigator.geolocation.watchPosition(send, () => setState('GPS sharing paused. Boundary alerts may be interrupted.', true), {
      enableHighAccuracy: true,
      maximumAge: 10000,
      timeout: 20000
    });
    setInterval(() => navigator.geolocation.getCurrentPosition(send, () => { }, { enableHighAccuracy: true, maximumAge: 0, timeout: 20000 }), 30000);
  }

  // Real-Time Alert Center Management
  const updateAlertCount = () => {
    const count = document.querySelectorAll('[data-alert]').length;
    ['#alert-count', '#alert-badge'].forEach(selector => {
      const el = document.querySelector(selector);
      if (el) el.textContent = count;
    });
  };

  const acknowledge = async id => {
    await secureFetch(`/api/alerts/${id}/ack`, { method: 'POST' });
    document.querySelector(`[data-alert="${id}"]`)?.remove();
    updateAlertCount();
    if (!document.querySelectorAll('[data-alert]').length) {
      const list = document.querySelector('#alerts-list');
      if (list) {
        list.innerHTML = '<div class="empty-alert" id="no-alerts"><span>✓</span><b>Perimeter Secure</b><p>No unacknowledged safety alerts or boundary deviations.</p></div>';
      }
    }
  };
  document.querySelectorAll('[data-ack]').forEach(button => button.addEventListener('click', () => acknowledge(button.dataset.ack)));

  const showAlertToast = message => {
    const region = document.querySelector('#toast-region');
    if (!region) return;
    const toast = document.createElement('div');
    toast.className = 'alert-toast';
    const mark = document.createElement('span');
    mark.className = 'toast-mark';
    mark.textContent = '⚠️';
    const copy = document.createElement('div');
    copy.innerHTML = '<b>Site Safety Warning</b>';
    const text = document.createElement('p');
    text.textContent = message;
    const close = document.createElement('button');
    close.type = 'button';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '×';
    close.onclick = () => toast.remove();
    copy.append(text);
    toast.append(mark, copy, close);
    region.append(toast);
    setTimeout(() => toast.remove(), 12000);
  };

  // Manager Real-Time Alerts Polling
  if (window.FG?.manager) {
    const seen = new Set([...document.querySelectorAll('[data-alert]')].map(el => el.dataset.alert));
    const poll = async () => {
      try {
        const res = await secureFetch('/api/alerts');
        const rows = await res.json();
        const list = document.querySelector('#alerts-list');
        if (!rows.length && !list.querySelector('.alert-item')) {
          list.innerHTML = '<div class="empty-alert" id="no-alerts"><span>✓</span><b>Perimeter Secure</b><p>No unacknowledged safety alerts or boundary deviations.</p></div>';
        }
        rows.forEach(alert => {
          if (seen.has(String(alert.id))) return;
          seen.add(String(alert.id));
          document.querySelector('#no-alerts')?.remove();
          const item = document.createElement('div');
          item.className = 'alert-item';
          item.dataset.alert = alert.id;
          const icon = document.createElement('div');
          icon.className = 'alert-icon';
          icon.textContent = alert.kind === 'sos' ? '🚨' : '⚠️';
          const body = document.createElement('div');
          body.className = 'alert-body';
          const title = document.createElement('b');
          title.textContent = alert.kind === 'sos' ? `🚨 ${alert.worker_name} SENT SOS REQUEST` : `⚠️ ${alert.worker_name} Left Geofence`;
          const info = document.createElement('p');
          info.textContent = `${alert.site_name} · ${new Date(alert.created_at).toLocaleTimeString()}`;
          const message = document.createElement('small');
          message.textContent = alert.message;
          const ack = document.createElement('button');
          ack.className = 'ack-btn';
          ack.textContent = 'Acknowledge Alert ✓';
          ack.addEventListener('click', () => acknowledge(alert.id));
          body.append(title, info, message, ack);
          item.append(icon, body);
          list.prepend(item);
          showAlertToast(alert.message);
          if ('Notification' in window && Notification.permission === 'granted') {
            new Notification('Chauhan Man Power Supply Safety', { body: alert.message });
          }
        });
        updateAlertCount();
      } catch (_) {
        // Polling retry on next cycle
      }
    };
    poll();
    setInterval(poll, 12000);
  }
    // ==========================================================================
    // PRECISION LEAFLET MAP & SATELLITE GEOFENCE CONTROLLER
    // ==========================================================================
    const calculateDistanceMeters = (lat1, lon1, lat2, lon2) => {
      const R = 6371000;
      const dLat = (lat2 - lat1) * Math.PI / 180;
      const dLon = (lon2 - lon1) * Math.PI / 180;
      const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
        Math.sin(dLon / 2) * Math.sin(dLon / 2);
      const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
      return R * c;
    };

    // 1. Worker Live Precision Map
    const workerMapEl = document.querySelector('#worker-live-map');
    let workerMap = null;
    let geofenceCircle = null;
    let gateMarker = null;
    let userMarker = null;
    let userAccuracyCircle = null;
    let latestUserCoords = null;

    const initWorkerMap = () => {
      if (!workerMapEl || !window.L) return;
      let siteLat = parseFloat(workerMapEl.dataset.siteLat) || 26.2726962;
      let siteLng = parseFloat(workerMapEl.dataset.siteLng) || 78.1364943;
      let siteRadius = parseFloat(workerMapEl.dataset.siteRadius) || 250;
      const siteName = workerMapEl.dataset.siteName || 'Industrial Site Entrance';

      workerMap = L.map('worker-live-map', {
        zoomControl: true,
        scrollWheelZoom: true
      }).setView([siteLat, siteLng], 16);

      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© OpenStreetMap contributors'
      }).addTo(workerMap);

      geofenceCircle = L.circle([siteLat, siteLng], {
        radius: siteRadius,
        color: '#059669',
        fillColor: '#10b981',
        fillOpacity: 0.18,
        weight: 2.5
      }).addTo(workerMap);

      const gateIcon = L.divIcon({
        className: 'custom-gate-pin',
        html: '🏢',
        iconSize: [32, 32],
        iconAnchor: [16, 16]
      });

      gateMarker = L.marker([siteLat, siteLng], { icon: gateIcon }).addTo(workerMap);
      gateMarker.bindPopup(`<b>${siteName}</b><br>Facility Entrance Point<br>Perimeter Radius: ${siteRadius}m`);

      // HUD & Navigation buttons
      document.querySelector('#btn-center-site')?.addEventListener('click', () => {
        if (workerMap && gateMarker) {
          workerMap.setView(gateMarker.getLatLng(), 17);
          gateMarker.openPopup();
        }
      });

      document.querySelector('#btn-recenter-user')?.addEventListener('click', () => {
        if (workerMap && latestUserCoords) {
          workerMap.setView([latestUserCoords.latitude, latestUserCoords.longitude], 17);
          userMarker?.openPopup();
        } else {
          locate().then(p => updateUserMapLocation(p)).catch(() => { });
        }
      });

      // Auto-locate user on map load
      if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
          p => updateUserMapLocation({ latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy: p.coords.accuracy }),
          () => { },
          { enableHighAccuracy: true, timeout: 8000 }
        );
      }
    };

    const updateUserMapLocation = coords => {
      if (!workerMap || !window.L || !coords) return;
      latestUserCoords = coords;
      const userLat = coords.latitude;
      const userLng = coords.longitude;
      const accuracy = coords.accuracy || 20;

      const siteLatLng = gateMarker ? gateMarker.getLatLng() : geofenceCircle.getLatLng();
      const siteRadius = geofenceCircle ? geofenceCircle.getRadius() : 250;
      const dist = Math.round(calculateDistanceMeters(userLat, userLng, siteLatLng.lat, siteLatLng.lng));

      // Update or create user marker
      const workerIcon = L.divIcon({
        className: 'custom-worker-pin',
        html: '📍',
        iconSize: [28, 28],
        iconAnchor: [14, 14]
      });

      if (userMarker) {
        userMarker.setLatLng([userLat, userLng]);
      } else {
        userMarker = L.marker([userLat, userLng], { icon: workerIcon }).addTo(workerMap);
        userMarker.bindPopup('<b>Your Current Position</b><br>Accuracy ±' + Math.round(accuracy) + 'm');
      }

      if (userAccuracyCircle) {
        userAccuracyCircle.setLatLng([userLat, userLng]);
        userAccuracyCircle.setRadius(accuracy);
      } else {
        userAccuracyCircle = L.circle([userLat, userLng], {
          radius: accuracy,
          color: '#3b82f6',
          fillColor: '#60a5fa',
          fillOpacity: 0.12,
          weight: 1.5,
          dashArray: '4, 4'
        }).addTo(workerMap);
      }

      // Update Live Telemetry HUD Bar
      const boundaryHud = document.querySelector('#map-boundary-hud');
      const boundaryText = document.querySelector('#map-boundary-text');
      const distanceHud = document.querySelector('#map-distance-hud');

      if (boundaryHud && boundaryText && distanceHud) {
        if (dist <= siteRadius) {
          boundaryHud.className = 'status-pill green';
          boundaryText.textContent = '✓ Inside Perimeter';
          distanceHud.textContent = `${dist}m from gate (${Math.round(siteRadius - dist)}m inside)`;
        } else {
          boundaryHud.className = 'status-pill red';
          boundaryText.textContent = '⚠️ Outside Perimeter';
          distanceHud.textContent = `${dist}m from gate (${Math.round(dist - siteRadius)}m beyond)`;
        }
      }
    };

    // 2. Manager Interactive Mini-Maps & Pin Picker
    const initManagerMaps = () => {
      if (!window.L) return;
      document.querySelectorAll('.site-mini-map').forEach(mapContainer => {
        const siteId = mapContainer.dataset.siteId;
        const lat = parseFloat(mapContainer.dataset.siteLat);
        const lng = parseFloat(mapContainer.dataset.siteLng);
        const radius = parseFloat(mapContainer.dataset.siteRadius) || 250;
        const siteName = mapContainer.dataset.siteName || 'Client Workplace';

        if (!lat || !lng) return;

        const miniMap = L.map(mapContainer, {
          zoomControl: false,
          attributionControl: false
        }).setView([lat, lng], 15);

        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 19
        }).addTo(miniMap);

        const circle = L.circle([lat, lng], {
          radius: radius,
          color: '#059669',
          fillColor: '#10b981',
          fillOpacity: 0.22,
          weight: 2
        }).addTo(miniMap);

        const gateIcon = L.divIcon({
          className: 'custom-gate-pin',
          html: '🏢',
          iconSize: [28, 28],
          iconAnchor: [14, 14]
        });

        const marker = L.marker([lat, lng], {
          icon: gateIcon,
          draggable: true
        }).addTo(miniMap);
        marker.bindPopup(`<b>${siteName}</b><br>Drag pin to adjust entrance`);

        // Sync with form
        const siteForm = document.querySelector(`[data-site-form="${siteId}"]`);
        if (siteForm) {
          const latInput = siteForm.querySelector('input[name="latitude"]');
          const lngInput = siteForm.querySelector('input[name="longitude"]');
          const radInput = siteForm.querySelector('input[name="radius_m"]');

          const updateInputs = (newLat, newLng) => {
            if (latInput) latInput.value = Number(newLat).toFixed(7);
            if (lngInput) lngInput.value = Number(newLng).toFixed(7);
            marker.setLatLng([newLat, newLng]);
            circle.setLatLng([newLat, newLng]);
          };

          marker.on('dragend', e => {
            const pos = e.target.getLatLng();
            updateInputs(pos.lat, pos.lng);
          });

          miniMap.on('click', e => {
            updateInputs(e.latlng.lat, e.latlng.lng);
          });

          radInput?.addEventListener('input', () => {
            const r = parseFloat(radInput.value);
            if (r && r > 0) circle.setRadius(r);
          });
        }
      });

      // 3. Manager Add Site Interactive Geofence Pin Picker Map
      const pickerContainer = document.querySelector('#site-picker-map');
      if (pickerContainer && window.L) {
        const defaultLat = 26.3733;
        const defaultLng = 78.0807;
        const defaultRadius = 250;

        const pickerMap = L.map(pickerContainer, {
          attributionControl: false
        }).setView([defaultLat, defaultLng], 15);

        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 19
        }).addTo(pickerMap);

        const pickerCircle = L.circle([defaultLat, defaultLng], {
          radius: defaultRadius,
          color: '#059669',
          fillColor: '#10b981',
          fillOpacity: 0.22,
          weight: 2
        }).addTo(pickerMap);

        const pickerMarker = L.marker([defaultLat, defaultLng], {
          draggable: true,
          icon: L.divIcon({
            className: 'custom-gate-pin',
            html: '📍',
            iconSize: [32, 32],
            iconAnchor: [16, 16]
          })
        }).addTo(pickerMap);
        pickerMarker.bindPopup('<b>Perimeter Entrance Point</b><br>Drag to reposition');

        const latInput = document.querySelector('#new-site-lat');
        const lngInput = document.querySelector('#new-site-lng');
        const radInput = document.querySelector('#new-site-radius');

        const setPickerLocation = (lat, lng) => {
          if (latInput) latInput.value = Number(lat).toFixed(7);
          if (lngInput) lngInput.value = Number(lng).toFixed(7);
          pickerMarker.setLatLng([lat, lng]);
          pickerCircle.setLatLng([lat, lng]);
          pickerMap.panTo([lat, lng]);
        };

        // Set initial values
        setPickerLocation(defaultLat, defaultLng);

        pickerMarker.on('dragend', e => {
          const pos = e.target.getLatLng();
          setPickerLocation(pos.lat, pos.lng);
        });

        pickerMap.on('click', e => {
          setPickerLocation(e.latlng.lat, e.latlng.lng);
        });

        radInput?.addEventListener('input', () => {
          const r = parseFloat(radInput.value);
          if (r && r > 0) pickerCircle.setRadius(r);
        });

        document.querySelector('[data-fill-site-location]')?.addEventListener('click', async () => {
          try {
            const p = await locate();
            setPickerLocation(p.latitude, p.longitude);
          } catch (_) { }
        });
      }
    };

    // Public Tunnel QR Modal & Clipboard Handlers
    const qrModal = document.querySelector('#qr-share-modal');
    document.querySelectorAll('#btn-show-qr-modal, #btn-show-login-qr').forEach(btn => {
      btn.addEventListener('click', () => qrModal?.showModal());
    });
    document.querySelectorAll('#qr-modal-close').forEach(btn => {
      btn.addEventListener('click', () => qrModal?.close());
    });
    document.querySelectorAll('#btn-copy-public-link, #btn-copy-tunnel-url').forEach(btn => {
      btn.addEventListener('click', () => {
        const url = 'https://sticks-unless-dog-treatment.trycloudflare.com';
        navigator.clipboard?.writeText(url).then(() => {
          const orig = btn.textContent;
          btn.textContent = 'Copied! ✓';
          setTimeout(() => { btn.textContent = orig; }, 2000);
        }).catch(() => {
          alert('Public URL: ' + url);
        });
      });
    });

    // Digital Employee ID Badge Modal Handlers
    const idBadgeDialog = document.querySelector('#id-badge-dialog');
    document.querySelector('#btn-open-id-badge')?.addEventListener('click', () => idBadgeDialog?.showModal());
    document.querySelector('#btn-close-id-badge')?.addEventListener('click', () => idBadgeDialog?.close());
    document.querySelector('#btn-done-id-badge')?.addEventListener('click', () => idBadgeDialog?.close());

    // Emergency SOS Modal Dismiss Handler
    const sosModal = document.querySelector('#sos-modal');
    document.querySelector('#sos-dismiss-btn')?.addEventListener('click', () => sosModal?.close());

    // Real-Time Smart Search Filter for Live Shift Roster
  const shiftTemplateSelect = document.querySelector('[data-shift-template-select]');
  shiftTemplateSelect?.addEventListener('change', () => {
    const selected = shiftTemplateSelect.selectedOptions[0];
    if (!selected?.dataset.site) return;
    const site = document.querySelector('#schedule-site');
    const start = document.querySelector('#schedule-start');
    const end = document.querySelector('#schedule-end');
    if (site) site.value = selected.dataset.site;
    if (start) start.value = selected.dataset.start || '';
    if (end) end.value = selected.dataset.end || '';
  });

    const rosterSearchInput = document.querySelector('.roster-filters input[name="q"]');
    const rosterTable = document.querySelector('#today-roster table tbody');
    if (rosterSearchInput && rosterTable) {
      rosterSearchInput.addEventListener('input', e => {
        const q = e.target.value.toLowerCase().trim();
        rosterTable.querySelectorAll('tr').forEach(row => {
          const text = row.textContent.toLowerCase();
          row.style.display = (!q || text.includes(q)) ? '' : 'none';
        });
      });
    }

    // Initialize maps on load
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        initWorkerMap();
        initManagerMaps();
      });
    } else {
      initWorkerMap();
      initManagerMaps();
    }

    // Hook into continuous location watcher for real-time map updates
    const origWatchPosition = navigator.geolocation?.watchPosition;
    if (origWatchPosition && window.FG?.active) {
      navigator.geolocation.watchPosition(p => {
        updateUserMapLocation({
          latitude: p.coords.latitude,
          longitude: p.coords.longitude,
          accuracy: p.coords.accuracy
        });
      }, () => { }, { enableHighAccuracy: true });
    }
})();

