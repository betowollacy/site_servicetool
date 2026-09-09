(function ($) {
  'use strict';

  var CONFIG = $.extend({
    authUrl: '',
    geoCountry: '',
    otpExpireDelay: 300,
    successReloadSeconds: 3,
    ajaxTimeout: 15000
  }, window.GSM_AUTH_CONFIG || {});

  var EMAIL = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/;
  var FADE_MS = 220;
  var ERROR_FADE_MS = 320;

  var ERR = {
    login: '#loginErrorMount',
    register: '#registerErrorMount',
    forgot: '#forgotErrorMount',
    otp: '#loginOtpErrorMount',
    passupdate: '#passUpdateErrorMount'
  };

  var PHP_MOUNT = {
    otp: '#loginOtpMount',
    loginOk: '#loginSuccessMount',
    registerOk: '#registerCheckEmailMount'
  };

  var $modal = $('#gsmAuthModal');
  var state = { registerStep: 1, loginEmail: '', otpTimer: null, reloadTimer: null };

  function apiUrl() {
    if (location.protocol === 'file:') {
      return CONFIG.authUrl;
    }
    return location.pathname.replace(/[^/]+$/, '') + CONFIG.authUrl.replace(/^\//, '');
  }

  function apiPost(data) {
    return $.ajax({
      url: apiUrl(),
      type: 'POST',
      data: data,
      timeout: CONFIG.ajaxTimeout,
      dataType: 'json'
    });
  }

  function phpHtml(res, viewId) {
    if (!res || !res.success || !res.html) {
      return '';
    }
    if (viewId && res.html.indexOf(viewId) === -1) {
      return '';
    }
    return res.html;
  }

  function escapeHtml(text) {
    return $('<div>').text(text).html();
  }

  function buildErrorHtml(message) {
    return '<div class="auth-error-banner" role="alert"><div class="auth-error-inner">' +
      '<i class="bi bi-exclamation-circle auth-error-icon" aria-hidden="true"></i>' +
      '<p class="auth-error-text mb-0">' + escapeHtml(message) + '</p></div></div>';
  }

  function errorFromXhr(xhr, fallback) {
    if (xhr.status === 0) {
      var msg = location.protocol === 'file:'
        ? 'Open http://localhost/ in your browser (Laragon must be running).'
        : 'Cannot reach the server. Start Laragon and open http://localhost/';
      return buildErrorHtml(msg);
    }
    if (xhr.status === 401 || xhr.status === 403) {
      return buildErrorHtml('Invalid email or password.');
    }
    var json = xhr.responseJSON;
    if (!json) {
      try { json = JSON.parse(xhr.responseText || ''); } catch (e) { json = null; }
    }
    if (json && json.html) {
      return json.html;
    }
    if (json && json.message) {
      return buildErrorHtml(json.message);
    }
    return buildErrorHtml(fallback);
  }

  function showErrorAt(selector, html) {
    var $mount = $(selector);
    if (!$mount.length) {
      return;
    }
    var $old = $mount.find('.auth-error-banner');

    function show() {
      $mount.html(html);
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          $mount.find('.auth-error-banner').addClass('show');
        });
      });
    }

    if ($old.length) {
      $old.removeClass('show');
      setTimeout(show, ERROR_FADE_MS);
    } else {
      show();
    }
  }

  function hideErrorAt(selector) {
    var $mount = $(selector);
    if (!$mount.length) {
      return;
    }
    var $banner = $mount.find('.auth-error-banner');
    if (!$banner.length) {
      $mount.empty();
      return;
    }
    $banner.removeClass('show');
    setTimeout(function () { $mount.empty(); }, ERROR_FADE_MS);
  }

  function showError(name, message) {
    showErrorAt(ERR[name], buildErrorHtml(message));
  }

  function showServerError(name, xhr, fallback) {
    showErrorAt(ERR[name], errorFromXhr(xhr, fallback));
  }

  function hideError(name) {
    hideErrorAt(ERR[name]);
  }

  function hideAllErrors() {
    $.each(ERR, function (_, sel) { hideErrorAt(sel); });
  }

  function hideAllViews() {
    $modal.find('.auth-view').removeClass('show').addClass('d-none');
  }

  function showView($el) {
    if (!$el.length) {
      return;
    }
    $el.removeClass('d-none');
    requestAnimationFrame(function () { $el.addClass('show'); });
  }

  function clearPhpMounts() {
    if (state.reloadTimer) {
      clearInterval(state.reloadTimer);
      state.reloadTimer = null;
    }
    if (state.otpTimer) {
      clearInterval(state.otpTimer);
      state.otpTimer = null;
    }
    $(PHP_MOUNT.otp + ',' + PHP_MOUNT.loginOk + ',' + PHP_MOUNT.registerOk).empty();
  }

  function scrollFormTop() {
    var el = document.getElementById('authFormScroll');
    if (el) {
      el.scrollTop = 0;
    }
  }

  function loadPhpView(mount, html, onReady) {
    clearPhpMounts();
    hideAllViews();
    $(mount).html(html);
    showView($(mount).find('.auth-view').first());
    scrollFormTop();
    if (onReady) {
      onReady();
    }
  }

  function switchView(name) {
    clearPhpMounts();
    hideAllErrors();

    var $next = $('#view-' + name);
    var $current = $modal.find('.auth-view.show');

    function afterSwitch() {
      if (name === 'register') {
        resetRegister();
      } else if (name === 'login') {
        resetLogin();
      }
      updateButtons();
      scrollFormTop();
      setTimeout(function () {
        $next.find('.form-control, .form-select').first().trigger('focus');
      }, FADE_MS + 40);
    }

    if ($current.length && !$current.is($next)) {
      $current.removeClass('show');
      setTimeout(function () {
        hideAllViews();
        showView($next);
        afterSwitch();
      }, FADE_MS);
      return;
    }

    hideAllViews();
    showView($next);
    afterSwitch();
  }

  function validEmail(value) {
    return EMAIL.test($.trim(value));
  }

  function passwordRules(password) {
    return {
      length: password.length >= 8,
      lower: /[a-z]/.test(password),
      upper: /[A-Z]/.test(password),
      special: /[^a-zA-Z0-9]/.test(password)
    };
  }

  function validPassword(password) {
    var r = passwordRules(password);
    return r.length && r.lower && r.upper && r.special;
  }

  function isFieldValid($field) {
    if ($field.is(':checkbox')) {
      return !$field.prop('required') || $field.is(':checked');
    }
    if ($field.hasClass('email-input')) {
      return validEmail($field.val());
    }
    if ($field.is('#regPassword')) {
      return validPassword($field.val());
    }
    if ($field.is('#regCountry')) {
      return $.trim($field.val()) !== '';
    }
    return $field[0].checkValidity();
  }

  function validateForm($form) {
    var ok = true;
    $form.find('.form-control, .form-select').each(function () {
      var $f = $(this);
      $f.removeClass('is-invalid');
      if (!isFieldValid($f)) {
        $f.addClass('is-invalid');
        ok = false;
      }
    });
    return ok;
  }

  function validateRegisterStep(step) {
    var ok = true;
    $('.register-step[data-register-step="' + step + '"]')
      .find('.form-control, .form-select, [type="checkbox"]')
      .each(function () {
        var $f = $(this);
        $f.removeClass('is-invalid');
        if (!isFieldValid($f)) {
          $f.addClass('is-invalid');
          ok = false;
        }
      });
    return ok;
  }

  function registerStepReady(step) {
    var ready = true;
    $('.register-step[data-register-step="' + step + '"]').find('[required]').each(function () {
      if (!isFieldValid($(this))) {
        ready = false;
        return false;
      }
    });
    return ready;
  }

  function formatCountry(item) {
    if (!item.id) {
      return item.text;
    }
    var url = $(item.element).attr('data-flag-url') ||
      '/common/flags/' + encodeURIComponent(String(item.id).toLowerCase()) + '.png';
    return $('<span class="d-inline-flex align-items-center gap-2"></span>')
      .append($('<img>', { src: url, alt: '', width: 20, height: 15, class: 'rounded-1' }))
      .append($('<span></span>').text(item.text));
  }

  function defaultCountryCode() {
    return String(CONFIG.geoCountry || $('#regCountry').attr('data-default-country') || '');
  }

  function ensureDefaultCountry() {
    var $country = $('#regCountry');
    var code = defaultCountryCode();
    if (!$country.length || !code) {
      return;
    }
    $country.val(code).removeClass('is-invalid');
  }

  function destroyCountrySelect() {
    var $country = $('#regCountry');
    if ($country.length && $country.hasClass('select2-hidden-accessible')) {
      $country.off('.regCountry').select2('destroy');
    }
  }

  function initCountrySelect() {
    var $country = $('#regCountry');
    if (!$country.length || typeof $.fn.select2 !== 'function') {
      return;
    }
    if ($country.closest('.register-step').hasClass('d-none')) {
      return;
    }

    destroyCountrySelect();
    ensureDefaultCountry();

    $country.select2({
      theme: 'bootstrap-5',
      width: '100%',
      dropdownParent: $modal,
      minimumResultsForSearch: 0,
      templateResult: formatCountry,
      templateSelection: formatCountry,
      escapeMarkup: function (m) { return m; }
    }).on('change.regCountry', function () {
      $(this).removeClass('is-invalid');
      hideError('register');
      updateButtons();
    }).on('select2:open.regCountry', function () {
      $modal.find('.select2-search__field').attr('autocomplete', 'off');
    });

    $country.val(defaultCountryCode()).trigger('change');
  }

  function resetCountrySelect() {
    destroyCountrySelect();
    ensureDefaultCountry();
  }

  function updatePasswordUi() {
    var rules = passwordRules($('#regPassword').val());
    var passed = 0;

    $('#regPasswordRules .pwd-rule').each(function () {
      var rule = $(this).data('rule');
      var ok = rules[rule];
      if (ok) {
        passed += 1;
      }
      $(this).toggleClass('text-success', ok).toggleClass('text-body-secondary', !ok);
      $(this).find('.pwd-rule-icon')
        .removeClass('bi-circle bi-check-circle-fill text-success text-body-secondary')
        .addClass(ok ? 'bi-check-circle-fill text-success' : 'bi-circle text-body-secondary');
    });

    var pct = passed * 25;
    var $bar = $('.pwd-strength-bar');
    var $label = $('.pwd-strength-label');
    if (!$bar.length) {
      return;
    }

    $bar.css('width', pct + '%').attr('aria-valuenow', pct);
    $bar.removeClass('bg-secondary bg-warning bg-success');
    if (passed === 0) {
      $bar.addClass('bg-secondary');
      $label.text('Enter a password').removeClass('text-success text-warning').addClass('text-body-secondary');
    } else if (passed < 4) {
      $bar.addClass('bg-warning');
      $label.text('Weak').removeClass('text-success text-body-secondary').addClass('text-warning');
    } else {
      $bar.addClass('bg-success');
      $label.text('Strong').removeClass('text-warning text-body-secondary').addClass('text-success');
    }
  }

  function updateButtons() {
    var $login = $('#loginForm');
    if ($login.length) {
      $login.find('[type="submit"]').prop('disabled', !(
        validEmail($('#loginEmail').val()) && $.trim($('#loginPassword').val())
      ));
    }

    var $forgot = $('#forgotForm');
    if ($forgot.length) {
      $forgot.find('[type="submit"]').prop('disabled', !validEmail($('#forgotEmail').val()));
    }

    $('.register-step').each(function () {
      var step = $(this).data('register-step');
      $(this).find('.register-next, [type="submit"]').prop('disabled', !registerStepReady(step));
    });

    updatePasswordUi();
  }

  function ensureTermsChecked() {
    var $terms = $('#regTerms');
    if ($terms.length) {
      $terms.prop({ checked: true, defaultChecked: true });
    }
  }

  function updateRegisterStep() {
    var $next = $('.register-step[data-register-step="' + state.registerStep + '"]');
    var $current = $('.register-step.show');

    function apply() {
      if ($current.length && String($current.data('register-step')) === '2') {
        destroyCountrySelect();
      }

      $('.register-step').addClass('d-none').removeClass('show');
      $next.removeClass('d-none');
      if (state.registerStep === 3) {
        ensureTermsChecked();
      }
      requestAnimationFrame(function () {
        $next.addClass('show');
        if (state.registerStep === 2) {
          setTimeout(initCountrySelect, 0);
        } else if (state.registerStep === 3) {
          setTimeout(function () { $('#regPassword').trigger('focus'); }, 0);
        }

        if(state.registerStep != 1){
          $('#backToLogin').addClass('d-none');
        }
        else{
          $('#backToLogin').removeClass('d-none');
        }

      });
      $('.reg-step-current').text(state.registerStep);
      updateButtons();
    }

    if ($current.length && !$current.is($next)) {
      $current.removeClass('show');
      setTimeout(apply, FADE_MS);
      return;
    }
    apply();
  }


  function resetLogin() {
    var $form = $('#loginForm');
    if (!$form.length) {
      return;
    }
    $form[0].reset();
    $('#truestMe').prop('checked', true);
    $form.find('.form-control').removeClass('is-invalid');
    updateButtons();
  }

  function resetRegister() {
    state.registerStep = 1;
    var $form = $('#registerForm');
    if (!$form.length) {
      return;
    }
    $form[0].reset();
    ensureTermsChecked();
    resetCountrySelect();
    $form.find('.form-control, .form-select').removeClass('is-invalid');
    updateRegisterStep();
  }

  function resetModal() {
    state.loginEmail = '';
    state.registerStep = 1;
    hideAllErrors();
    clearPhpMounts();
    destroyCountrySelect();
    resetLogin();
    var $forgot = $('#forgotForm');
    if ($forgot.length) {
      $forgot[0].reset();
    }
    resetRegister();
    hideAllViews();
    showView($('#view-login'));
    scrollFormTop();
  }

  

  function onAuthSubmit(e) {
    e.preventDefault();
   
    var $form = $(this);
    var $submitBtn = $(e.originalEvent.submitter);
    var $submitBtnText = $.trim($submitBtn.text());
    var $submitBtnLoader = '<span class="spinner-border spinner-border-sm spinner-xs me-1" role="status" aria-hidden="true"></span>';

    var payload = {};

    if ($form.attr('id') === 'loginForm') {
      payload['action'] = 'customer_login_request';
      payload['fingerprint'] = getDeviceFingerprint();
      var errorAction = 'login';
      
    }
    else if($form.attr('id') === 'loginOtpForm'){
      payload['action'] = 'otp_verify_request';
      payload['fingerprint'] = getDeviceFingerprint();
      var errorAction = 'otp';
    }
    else if($form.attr('id') === 'registerForm'){
      payload['action'] = 'customer_register_request';
      payload['fingerprint'] = getDeviceFingerprint();
      var errorAction = 'register';
    }
    else if($form.attr('id') === 'forgotForm'){
      payload['action'] = 'customer_foget_request';
      payload['fingerprint'] = getDeviceFingerprint();
      var errorAction = 'forgot';
    }
    else if($form.attr('id') === 'passwordUpdateForm'){
      payload['action'] = 'customer_passupdate_request';
      payload['fingerprint'] = getDeviceFingerprint();
      var errorAction = 'passupdate';
    }
    else{
      return;
    }

    hideError(errorAction);

    $.each($form.serializeArray(), function(_, field) {
        payload[field.name] = field.value;
    });

    $form.find('input[type="checkbox"]').each(function() {
      payload[this.name] = this.checked ? 1 : 0;
    });
  
    $.ajax({
      url: '/customer/auth',
      method: 'POST',
      data: payload,
      beforeSend: function() {
        $submitBtn.html($submitBtnLoader + ' ' + $submitBtnText).prop('disabled', true);
      },
      complete: function() {
        setTimeout(function() {
          $submitBtn.html($submitBtnText).prop('disabled', false);
        }, 200);
      },
      success: function(response) {

        if (response.success) {

          if(response.next === 'login-success'){
            loadPhpView(PHP_MOUNT.loginOk, response.html, function () {
              var sec = CONFIG.successReloadSeconds;
              var $view = $('#view-login-success');
              var $count = $view.find('.success-reload-count');
              function reloadNow() {
                if (state.reloadTimer) {
                  clearInterval(state.reloadTimer);
                  state.reloadTimer = null;
                }
                location.reload();
              }
              $count.text(sec);
              state.reloadTimer = setInterval(function () {
                $count.text(sec);
                if (sec-- <= 0) {
                  reloadNow();
                }
              }, 1000);
              $view.find('.success-continue-btn').one('click', reloadNow);
            });

          }
          else if(response.next === 'login-otp'){
            loadPhpView(PHP_MOUNT.otp, response.html, function () {
              $('#loginOtp1').trigger('focus');
            });
          }
          else if(response.next === 'registration-success'){
            loadPhpView(PHP_MOUNT.registerOk, response.html);
          }
          else if(response.next === 'pass-updated'){
            $('#password-update-html').html(response.html)
          }
          
        }
        else{
          showError(errorAction, response.message);
        }
      },
      error: function(xhr, status, error) {
        let message = 'Error occurred while fetching..';
        if (xhr.responseJSON && xhr.responseJSON.message) {
            message = xhr.responseJSON.message;
        } else if (xhr.responseText) {
            message = xhr.responseText;
        }
        showError(errorAction, message);
      }
    });

  }

  

  
  $(function () {
    ensureTermsChecked();
    var openView = 'login';

    $(document).on('click', '[data-switch-view]', function (e) {
      e.preventDefault();
      switchView($(this).data('switch-view'));
    });

    $(document).on('click', '[data-bs-toggle="modal"][data-auth-view]', function () {
      openView = $(this).data('auth-view') || 'login';
    });

    $modal.on('shown.bs.modal', function () {
      $modal.find('input, select, textarea').attr('autocomplete', 'off');
      switchView(openView);
      openView = 'login';
    }).on('hidden.bs.modal', resetModal);

    var $currency = $('#regCurrency');
    $currency.select2({
      theme: 'bootstrap-5',
      width: '100%',
      dropdownParent: $modal,
      minimumResultsForSearch: 0,
      templateResult: function(data) {
        return $('<span style="font-size:14px">' + data.text + '</span>');
      },
      templateSelection: function(data) {
        return $('<span style="font-size:14px">' + data.text + '</span>');
      },
    }).on('select2:open.regCurrency', function () {
      $modal.find('.select2-search__field').attr('autocomplete', 'off');
    });

    $(document)
      .on('click', '.register-next', function () {
        if ($(this).prop('disabled') || state.registerStep >= 3) {
          return;
        }
        if (validateRegisterStep(state.registerStep)) {
          hideError('register');
          state.registerStep += 1;
          updateRegisterStep();
        } else {
          showError('register', 'Please complete all required fields.');
        }
      })
      .on('click', '.register-prev', function () {
        if (state.registerStep <= 1) {
          return;
        }
        hideError('register');
        state.registerStep -= 1;
        updateRegisterStep();
      })
      .on('click', '.toggle-password', function () {
        var $input = $($(this).data('target'));
        var show = $input.attr('type') === 'password';
        $input.attr('type', show ? 'text' : 'password');
        $(this).find('i').toggleClass('bi-eye', !show).toggleClass('bi-eye-slash', show);
      })
      .on('click', '.otp-resend', function (e) {
        e.preventDefault();
      })
      .on('input', '.email-input', function () {
        $(this).val($(this).val().toLowerCase());
      })
      .on('input change', '#loginForm input, #forgotForm input, #registerForm input, #registerForm select', function () {
        var formId = $(this).closest('form').attr('id');
        if (formId === 'loginForm') {
          hideError('login');
        } else if (formId === 'forgotForm') {
          hideError('forgot');
        } else if (formId === 'registerForm') {
          hideError('register');
        }
        updateButtons();
      })
      .on('input', '.otp-input', function () {
        var $input = $(this);
        var $all = $input.closest('.otp-inputs').find('.otp-input');
        var i = $all.index($input);

        $input.val($input.val().replace(/\D/g, '').slice(0, 1));
        if ($input.val() && i < $all.length - 1) {
          $all.eq(i + 1).focus();
        }

        var complete = true;
        $input.closest('form').find('.otp-input').each(function () {
          if (!/^\d$/.test($(this).val())) {
            complete = false;
          }
        });
        $input.closest('form').find('[type="submit"]').prop('disabled', !complete);
        hideError('otp');
      })
      .on('keydown', '.otp-input', function (e) {
        if (e.key !== 'Backspace') {
          return;
        }
        var $input = $(this);
        var $all = $input.closest('.otp-inputs').find('.otp-input');
        if (!$input.val() && $all.index($input) > 0) {
          $all.eq($all.index($input) - 1).focus();
        }
      })
      .on('paste', '.otp-input', function (e) {
        var text = (e.originalEvent.clipboardData || window.clipboardData)
          .getData('text').replace(/\D/g, '').slice(0, 6);
        if (!text) {
          return;
        }
        e.preventDefault();
        var $inputs = $(this).closest('.otp-inputs').find('.otp-input');
        $inputs.each(function (j) { $(this).val(text.charAt(j) || ''); });
        $inputs.eq(Math.min(text.length, 5)).focus();
        $inputs.first().trigger('input');
      })
      .on('submit', '#loginForm', onAuthSubmit)
      .on('submit', '#loginOtpForm', onAuthSubmit)
      .on('submit', '#registerForm', onAuthSubmit)
      .on('submit', '#forgotForm', onAuthSubmit)
      .on('submit', '#passwordUpdateForm', onAuthSubmit);

    updateButtons();
  });

})(jQuery);