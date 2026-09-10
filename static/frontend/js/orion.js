/* Orion / SERVICETOOL — custom home interactions */
(function () {
    "use strict";

    /* ---------- 1. Constelação / fundo espaço ---------- */
    (function () {
        var canvas = document.getElementById('orion-space-canvas');
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var width, height, particles = [];
        var particleCount = 70;
        var connectionDistance = 150;
        var color = 'rgba(37, 99, 235,';

        function resize() {
            width = canvas.width = window.innerWidth;
            height = canvas.height = window.innerHeight;
        }
        window.addEventListener('resize', resize);
        resize();

        function Particle() {
            this.x = Math.random() * width;
            this.y = Math.random() * height;
            this.vx = (Math.random() - 0.5) * 0.5;
            this.vy = (Math.random() - 0.5) * 0.5;
            this.size = Math.random() * 2 + 1;
        }
        Particle.prototype.update = function () {
            this.x += this.vx;
            this.y += this.vy;
            if (this.x < 0 || this.x > width) this.vx *= -1;
            if (this.y < 0 || this.y > height) this.vy *= -1;
        };
        Particle.prototype.draw = function () {
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
            ctx.fillStyle = color + '0.5)';
            ctx.fill();
        };

        for (var i = 0; i < particleCount; i++) particles.push(new Particle());

        function animate() {
            ctx.clearRect(0, 0, width, height);
            for (var i = 0; i < particles.length; i++) {
                particles[i].update();
                particles[i].draw();
                for (var j = i; j < particles.length; j++) {
                    var dx = particles[i].x - particles[j].x;
                    var dy = particles[i].y - particles[j].y;
                    var dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < connectionDistance) {
                        ctx.beginPath();
                        var opacity = 1 - (dist / connectionDistance);
                        ctx.strokeStyle = color + (opacity * 0.4) + ')';
                        ctx.lineWidth = 1;
                        ctx.moveTo(particles[i].x, particles[i].y);
                        ctx.lineTo(particles[j].x, particles[j].y);
                        ctx.stroke();
                    }
                }
            }
            requestAnimationFrame(animate);
        }
        animate();
    })();

    document.addEventListener('DOMContentLoaded', function () {
        /* ---------- 2. FAQ ---------- */
        var faqBtn = document.getElementById('orion-faq-btn');
        var faqBox = document.getElementById('orion-faq-box');

        if (faqBtn && faqBox) {
            faqBtn.addEventListener('click', function () {
                faqBox.style.display = (faqBox.style.display === 'block') ? 'none' : 'block';
            });
        }

        var questions = document.querySelectorAll('.faq-question');
        questions.forEach(function (q) {
            q.addEventListener('click', function () {
                q.parentElement.classList.toggle('active');
            });
        });

        var searchInput = document.getElementById('faq-search');
        var faqItems = document.querySelectorAll('.faq-item');
        if (searchInput) {
            searchInput.addEventListener('input', function (e) {
                var term = e.target.value.toLowerCase();
                faqItems.forEach(function (item) {
                    if (item.innerText.toLowerCase().indexOf(term) > -1) {
                        item.classList.remove('hidden');
                    } else {
                        item.classList.add('hidden');
                    }
                });
            });
        }

        /* ---------- 3. Modal de termos (obrigatório) ---------- */
        var modal = document.getElementById('orion-terms-modal');
        var check = document.getElementById('terms-check');
        var btn = document.getElementById('btn-agree');

        if (modal && check && btn) {
            var agreed = localStorage.getItem('orion_terms_v1');
            if (!agreed) {
                setTimeout(function () { modal.classList.add('show'); }, 500);
            }
            check.addEventListener('change', function () { btn.disabled = !check.checked; });
            btn.addEventListener('click', function () {
                localStorage.setItem('orion_terms_v1', 'true');
                modal.classList.remove('show');
            });
        }

        /* ---------- 4. Voltar ao topo ---------- */
        var backTop = document.getElementById('orion-back-to-top');
        if (backTop) {
            window.addEventListener('scroll', function () {
                if (window.scrollY > 300) backTop.classList.add('show');
                else backTop.classList.remove('show');
            });
            backTop.addEventListener('click', function () { window.scrollTo({ top: 0, behavior: 'smooth' }); });
        }

        /* ---------- 5. Notificações de depósito ---------- */
        var notifArea = document.getElementById('orion-notif-area');
        if (notifArea && localStorage.getItem('orion_terms_v1')) {
            setTimeout(function () { showToast(); }, 5000);
            setInterval(showToast, 20000);
        }

        function showToast() {
            if (!notifArea) return;
            var names = ['João P.', 'Maria S.', 'Carlos A.', 'Ana L.', 'Pedro R.', 'Lucas M.', 'Fernanda T.'];
            var amounts = ['50.00', '30.00', '100.00', '75.00', '25.00', '120.00', '60.00'];
            var name = names[Math.floor(Math.random() * names.length)];
            var amount = amounts[Math.floor(Math.random() * amounts.length)];

            var toast = document.createElement('div');
            toast.className = 'orion-toast';
            toast.innerHTML =
                '<div class="toast-icon"><i class="fas fa-bolt"></i></div>' +
                '<div><strong>NOVO PIX RECEBIDO</strong><br>' +
                '<span style="font-size:12px; color:#97a1c4">' + name + ' depositou R$ ' + amount + '</span></div>' +
                '<span class="toast-close-hint">clique fechar</span>';

            notifArea.appendChild(toast);
            requestAnimationFrame(function () { toast.classList.add('visible'); });

            function removeToast() {
                toast.classList.remove('visible');
                setTimeout(function () { toast.remove(); }, 500);
            }
            toast.addEventListener('click', removeToast);
            setTimeout(function () { if (document.body.contains(toast)) removeToast(); }, 6000);
        }
    });
})();
