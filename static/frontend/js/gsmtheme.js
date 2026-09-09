$(window).on('load', function () {
  setTimeout(function () {
      $('#gsmtheme-loader').remove();
      $('#gsmtheme-content').removeClass('d-none').hide().fadeIn(200);
  }, 1000);
});

function getDeviceFingerprint() {
  try {

    let deviceId = localStorage.getItem('device_id');

    if (!deviceId) {
      if (window.crypto && crypto.randomUUID) {
        deviceId = crypto.randomUUID();
      } else {
        deviceId = 'dev-' + Math.random().toString(36).slice(2) + Date.now();
      }
      localStorage.setItem('device_id', deviceId);
    }

    const data = {
      device_id: deviceId,
      user_agent: navigator.userAgent || '',
      platform: navigator.platform || '',
      language: navigator.language || '',
      timezone: (
        Intl &&
        Intl.DateTimeFormat &&
        Intl.DateTimeFormat().resolvedOptions
      )
        ? Intl.DateTimeFormat().resolvedOptions().timeZone
        : '',
      screen: (screen && screen.width && screen.height)
        ? screen.width + 'x' + screen.height
        : ''
    };

    return btoa(unescape(encodeURIComponent(JSON.stringify(data))));

  } catch (e) {

    return btoa(JSON.stringify({
      device_id: 'fallback-' + Date.now()
    }));
  }
}

const notyf = new Notyf({
  limit: 1,
  duration: 5000,
  dismissible: false,
  position: {
    x: 'left',
    y: 'bottom',
  },
  types: [
    {
      type: 'success',
      className: 'toast-250',
    },
    {
      type: 'error',
      className: 'toast-250',
    }
  ]
});

function successToast(message) {
  notyf.dismissAll();
  notyf.success(message);
}

function errorToast(message) {
  notyf.dismissAll();
  notyf.error(message);
}

function successModal(message) {
  $('#successModalText').text(message);
  $('#successModal').modal('show');
}

$('.menu-login-btn').on('click', function () {
  $('#menuOffcanvas').offcanvas('hide');
});

$(document).ajaxSend(function (event, jqxhr, settings) {
  NProgress.start()
});

$(document).ajaxComplete(function (event, jqxhr, settings) {
  NProgress.done()
});

const $wrap = $('#navWrap');
const $track = $('#navTrack');
const $arrowLeft = $('#arrowLeft');
const $arrowRight = $('#arrowRight');

let currentTX = 0;
let dragging = false, startX = 0, startTX = 0, moved = false;

function maxLeft() {
  const overflow = $track[0].offsetWidth - $wrap[0].offsetWidth;
  return overflow > 0 ? -overflow : 0;
}

function applyTX(x, animated) {
  currentTX = Math.min(0, Math.max(maxLeft(), x));
  if (animated) {
    $track.removeClass('no-transition');
  } else {
    $track.addClass('no-transition');
  }
  $track.css('transform', `translateX(${currentTX}px)`);
  updateArrows();
}

function updateArrows() {
  const ml = maxLeft();
  const hasOverflow = ml < 0;

  $arrowLeft.toggleClass('visible', hasOverflow && currentTX < 0);
  $arrowRight.toggleClass('visible', hasOverflow && currentTX > ml);
}

$arrowLeft.on('click', () => applyTX(currentTX + 160, true));
$arrowRight.on('click', () => applyTX(currentTX - 160, true));

$track.on('mousedown', function (e) {
  if (e.button !== 0) return;
  dragging = true; moved = false;
  startX = e.clientX; startTX = currentTX;
  $track.addClass('grabbing');
  e.preventDefault();
});

$(window).on('mousemove', function (e) {
  if (!dragging) return;
  const d = e.clientX - startX;
  if (Math.abs(d) > 3) moved = true;
  applyTX(startTX + d, false);
});

$(window).on('mouseup', function () {
  dragging = false;
  $track.removeClass('grabbing');
});

$track.find('a').on('click', function (e) {
  if (moved) { e.preventDefault(); moved = false; }
});

$track[0].addEventListener('touchstart', e => {
  startX = e.touches[0].clientX; startTX = currentTX; dragging = true;
}, { passive: true });
$track[0].addEventListener('touchmove', e => {
  if (!dragging) return;
  applyTX(startTX + (e.touches[0].clientX - startX), false);
}, { passive: true });
$track.on('touchend', () => { dragging = false; });

updateArrows();
$(window).on('resize', () => { applyTX(currentTX, false); });

let activeItem = null, hideTimer = null;

function showDrop(li) {
  clearTimeout(hideTimer);
  if (activeItem && activeItem !== li) closeDrop(activeItem, true);
  const $li = $(li);
  const $drop = $('#' + $li.data('drop'));
  if (!$drop.length) return;
  const r = li.getBoundingClientRect();
  $drop.css({ top: (r.bottom + 2) + 'px', left: r.left + 'px' })
    .addClass('visible');
  $li.addClass('is-open');
  activeItem = li;
}

function closeDrop(li, immediate) {
  const $li = $(li);
  const $drop = $('#' + $li.data('drop'));
  if (!$drop.length) return;
  const run = () => {
    $drop.removeClass('visible');
    $li.removeClass('is-open');
    if (activeItem === li) activeItem = null;
  };
  immediate ? run() : (hideTimer = setTimeout(run, 120));
}

$('.has-drop').each(function () {
  const li = this;
  const $li = $(li);
  const $drop = $('#' + $li.data('drop'));
  if (!$drop.length) return;

  $li.on('mouseenter', () => showDrop(li));
  $li.on('mouseleave', e => {
    if (!$drop[0].contains(e.relatedTarget)) closeDrop(li, false);
  });
  $drop.on('mouseenter', () => { clearTimeout(hideTimer); $li.addClass('is-open'); });
  $drop.on('mouseleave', e => {
    if (!$li[0].contains(e.relatedTarget)) closeDrop(li, false);
  });
});

$(document).on('click', function (e) {
  if (!$(e.target).closest('.has-drop').length && !$(e.target).closest('.gsm-dropdown').length) {
    $('.has-drop').each(function () { closeDrop(this, true); });
  }
});

function confirmLogout(element) {
  var logoutUrl = $(element).data('logout-url');
  if (confirm("Are you sure to logout?")) {
    window.location.href = logoutUrl;
  }
}

function countrySelect2(selector){
  $(selector).select2({
      theme: 'bootstrap-5',
      width: '100%',
      minimumResultsForSearch: 0,
      templateResult: formatCountryOption,
      templateSelection: formatCountryOption,
  });
  var currentCountry = $(selector).data('currentCountry');
  if (currentCountry) {
      $(selector).val(currentCountry).trigger('change');
  }
  $(selector).on('select2:open', function () {
      $('.select2-search__field')
          .attr('autocomplete', 'off')
          .on('focus blur', function () {
          $(this).css({ 'box-shadow': 'none', 'border-color': '#dcdde1' });
      });
      $('.select2-dropdown').css({
          'border-color': '#dcdde1',
          'box-shadow': 'none'
      });
  });
  $(selector).next('.select2-container').find('.select2-selection')
  .on('focus blur', function () {
      $(this).css({ 'box-shadow': 'none', 'border-color': '#dcdde1' });
  });
}
function formatCountryOption(state) {
  if (!state.id) {
      return state.text;
  }
  var flagUrl = $(state.element).data('flagUrl');
  if (!flagUrl) {
      return state.text;
  }
  return $(
      '<span style="display:flex;align-items:center;">' +
          '<img src="' + flagUrl + '" style="width:35px;height:22px;border-radius:5px;margin-right:10px;flex-shrink:0;" alt="" />' +
          '<span>' + state.text + '</span>' +
      '</span>'
  );
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch {
    const $t = $('<textarea>').val(text).css({ position: 'fixed', opacity: 0 }).appendTo('body');
    $t[0].select();
    const ok = document.execCommand('copy');
    $t.remove();
    return ok;
  }
}
$(document).off('click', '#copyReply').on('click', '#copyReply', async function(){
    const $btn = $(this);
    const $el = $('#' + $btn.data('copyTarget'));
    if (!(await copy($el.text().trim() || ''))) return;
    const $label = $btn.find('.btn-label');
    if (!$label.length) return;
    $btn.prop('disabled', true);
    $label.text('Copied!');

});


// FORM SUBMIT
const formIds = [
  'profileUpdateForm', 'passUpdateForm', 'verifyOtpForm', 'connectBotForm',
  'disconnectBotForm'
];
const selector = formIds.map(id => '#' + id).join(', ');
$(document).off('submit', selector).on('submit', selector, function(e){
  e.preventDefault();
  var $form = $(this);
  var $submitBtn = $(e.originalEvent.submitter);
  var $submitBtnText = $.trim($submitBtn.text());
  var $submitBtnLoader = '<span class="spinner-border spinner-border-sm spinner-xs me-1" role="status" aria-hidden="true"></span>';
  var payload = {};
  payload['action'] = this.id;
  $.each($form.serializeArray(), function(_, field) {
      payload[field.name] = field.value;
  });
  $form.find('input[type="checkbox"]').each(function() {
    payload[this.name] = this.checked ? 1 : 0;
  });
  $.ajax({
    url: '/customer/utility',
    method: 'POST',
    data: payload,
    beforeSend: function() {
      $submitBtn.html($submitBtnLoader + ' Loading...').prop('disabled', true);
    },
    complete: function() {
      setTimeout(function() {
        $submitBtn.html($submitBtnText).prop('disabled', false);
      }, 200);
    },
    success: function(response) {
      if (response.success) {
        successToast(response.message);
        if(payload['action'] === 'passUpdateForm'){
          $form[0].reset();
          setTimeout(function() {
              $submitBtn.html($submitBtnText).prop('disabled', true);
          }, 300);
        }
        else if(payload['action'] === 'verifyOtpForm'){
          $('.2fa-qrcode').hide();
          $('#app2Fa').prop('checked', true);
          $('#app2Fa').prop('disabled', false);
          $form[0].reset();
        }
        else if(payload['action'] === 'connectBotForm'){
          botConnectSuccess(response.data);
        }
        else if(payload['action'] === 'disconnectBotForm'){
          botDisconnectSuccess(response.data);
        }
      }
      else{
        errorToast(response.message);
      }
    },
    error: function(xhr, status, error) {
      let message = 'Error occurred while fetching..';
      if (xhr.responseJSON && xhr.responseJSON.message) {
          message = xhr.responseJSON.message;
      } else if (xhr.responseText) {
          message = xhr.responseText;
      }
      errorToast(message);
    }
  });
});

const checkBoxIds = [
  'app2Fa', 'toggleEmail2Fa', 'ipLogout', 'toggleApiAccess', 'order_received_mail',
  'order_success_mail', 'order_rejected_mail'
];
const checkBoxSelector = checkBoxIds.map(id => '#' + id).join(', ');
$(checkBoxSelector).off().on('change', function(){
  
  var checkbox = $(this);
  var value = checkbox.is(':checked') ? 1 : 0;
  var payload = {};
  payload['action'] = this.id;
  payload['value'] = value;

  function rollback(){
    checkbox.prop('checked', value === 1 ? false : true);
  }

  $.ajax({
    url: '/customer/utility',
    method: 'GET',
    data: payload,
    success: function(response) {
      if (response.success) {
        successToast(response.message);
        if(payload['action'] === 'app2Fa'){
          app2FaDisabled(response.data);
        }
        else if(payload['action'] === 'toggleApiAccess'){
          toggleApiAccessUi(response.data)
        }

      }
      else {
        errorToast(response.message);
        rollback();
      }
    },
    error: function(xhr, status, error) {
      let message = 'Error occurred while fetching..';
      if (xhr.responseJSON && xhr.responseJSON.message) {
          message = xhr.responseJSON.message;
      } else if (xhr.responseText) {
          message = xhr.responseText;
      }
      errorToast(message);
      rollback();
    }
  });

});

const onClickIds = [
  'changeAccessKey'
];
const onClickSelector = onClickIds.map(id => '#' + id).join(', ');
$(onClickSelector).off().on('click', function(){
  
  var $btn = $(this);
  var $btnText = $.trim($btn.text());
  var $btnLoader = '<span class="spinner-border spinner-border-sm spinner-xs me-1" role="status" aria-hidden="true"></span>';

  var payload = {};
  payload['action'] = this.id;

  $.ajax({
    url: '/customer/utility',
    method: 'GET',
    data: payload,
    beforeSend: function() {
      $btn.html($btnLoader + ' Loading...').prop('disabled', true);
    },
    complete: function() {
      setTimeout(function() {
        $btn.html($btnText).prop('disabled', false);
      }, 200);
    },
    success: function(response) {
      if (response.success) {
        successToast(response.message);
        
        if(payload['action'] === 'changeAccessKey'){
          apiKeyUpdate(response.data)
        }

      }
      else {
        errorToast(response.message);

      }
    },
    error: function(xhr, status, error) {
      let message = 'Error occurred while fetching..';
      if (xhr.responseJSON && xhr.responseJSON.message) {
          message = xhr.responseJSON.message;
      } else if (xhr.responseText) {
          message = xhr.responseText;
      }
      errorToast(message);

    }
  });

});

function easyDatatime(data, selector = null){
  if(selector){
    const val = $(selector).data('date');
    const date = new Date(val);
    const day = String(date.getDate()).padStart(2, '0');
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const year = date.getFullYear();
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const output =  `${day}-${month}-${year} ${hours}:${minutes}`;
    $(selector).text(val ? output : '-');
  }
  else{
    const date = new Date(data);
    const day = String(date.getDate()).padStart(2, '0');
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const year = date.getFullYear();
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    return `${day}-${month}-${year} ${hours}:${minutes}`;
  }
    
}






















