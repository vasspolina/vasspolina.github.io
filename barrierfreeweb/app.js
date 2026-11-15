// Plate carousel: the track scrolls natively (swipe, trackpad, arrow keys
// once focused); the buttons step one plate and the counter follows.
// Nothing moves by itself.
document.querySelectorAll("[data-plates]").forEach((root) => {
  const track = root.querySelector(".plates__track");
  const plates = [...track.children];
  const count = root.querySelector(".plates__count");
  const status = root.querySelector(".plates__status");
  let current = 0;

  const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const show = (i) => {
    current = (i + plates.length) % plates.length;
    track.scrollTo({ left: plates[current].offsetLeft - track.offsetLeft, behavior: reduce() ? "auto" : "smooth" });
    status.textContent = `${current + 1} of ${plates.length}: ${plates[current].textContent.trim()}`;
  };
  const sync = () => {
    current = Math.max(0, Math.min(plates.length - 1, Math.round(track.scrollLeft / track.clientWidth)));
    count.textContent = `${current + 1}/${plates.length}`;
  };

  let t;
  track.addEventListener("scroll", () => { clearTimeout(t); t = setTimeout(sync, 60); });
  root.querySelector("[data-prev]").addEventListener("click", () => show(current - 1));
  root.querySelector("[data-next]").addEventListener("click", () => show(current + 1));
  // A click on a plate moves to the next one (the last goes back to the
  // first). A drag that selected text is left alone. The buttons and the
  // arrow keys stay the way in for keyboard and screen-reader users.
  track.addEventListener("click", () => {
    if (String(getSelection && getSelection()).trim()) return;
    show(current + 1);
  });
  track.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") { e.preventDefault(); show(current + 1); }
    if (e.key === "ArrowLeft") { e.preventDefault(); show(current - 1); }
  });
  sync();
});

// "What we offer": rows that open in place. Clicking a row opens its copy
// under it and closes the one that was open; clicking it again closes it.
document.querySelectorAll("[data-offers]").forEach((root) => {
  const buttons = [...root.querySelectorAll(".acc__btn")];
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const opening = btn.getAttribute("aria-expanded") !== "true";
      buttons.forEach((b) => {
        const on = b === btn && opening;
        b.setAttribute("aria-expanded", on ? "true" : "false");
        document.getElementById(b.getAttribute("aria-controls")).hidden = !on;
      });
    });
  });
});

// Section buttons on long pages: the button for the section being read is
// marked aria-current, so it is filled and a screen reader hears "current".
document.querySelectorAll("[data-toc]").forEach((nav) => {
  const links = [...nav.querySelectorAll('a[href^="#"]')];
  const targets = links.map((a) => document.getElementById(decodeURIComponent(a.hash.slice(1))));
  let queued = false;
  const update = () => {
    queued = false;
    const line = innerHeight * 0.3;
    let current = 0;
    targets.forEach((t, i) => { if (t && t.getBoundingClientRect().top <= line) current = i; });
    if (innerHeight + scrollY >= document.documentElement.scrollHeight - 2) {
      // at the very end, the last section that is on screen is the one being read
      targets.forEach((t, i) => { if (t && t.getBoundingClientRect().top < innerHeight) current = Math.max(current, i); });
    }
    links.forEach((a, i) => {
      if (i === current) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
  };
  addEventListener("scroll", () => { if (!queued) { queued = true; requestAnimationFrame(update); } }, { passive: true });
  addEventListener("resize", update);
  update();
});

// The chat: answers come from Claude through the page's `sample` capability.
// Claude has no memory between calls, so every call sends the studio's facts
// and the whole conversation so far. Where the capability is missing (a
// signed-out viewer, a view that cannot ask Claude) the chat says so instead
// of failing quietly.
document.querySelectorAll("[data-chat]").forEach((root) => {
  const form = root.querySelector("form");
  const input = root.querySelector("textarea");
  const send = root.querySelector(".chat__send");
  const thread = root.querySelector(".chat__thread");
  const prompts = root.querySelector(".chat__prompts");
  const status = root.querySelector("[data-chat-status]");
  const history = [];
  let busy = false;
  const samplePromise = (window.claude && window.claude.use) ? window.claude.use("sample").catch(() => null) : Promise.resolve(null);

  const FACTS = `You answer questions on the website of BarrierFreeWeb, a design studio in Berlin that builds websites everyone can use. Founders: Liza Gavrova (Co-Founder & Managing Director) and Polina Vasilieva (Co-Founder & Designer). Contact: gf@barrierfreeweb.de.
Services: design and typography (text reflow, fluid design, semantic markup, alt text); UX/UI design (accessible components, robust code, status messages, color customization); brand development; content strategy; copywriting; accessibility testing by real people with disabilities, not only automated tools.
Facts the site states: since 28 June 2025 the European Accessibility Act (EAA) requires websites operating in the EU to meet accessibility requirements. Germany has the BFSG (Barrierefreiheitsstärkungsgesetz), the UK enforces accessibility through the Equality Act 2010; all point to WCAG 2.1 Level AA. Fines reach up to €100,000 in Germany and over €1,000,000 in some EU countries; consumers and advocacy groups can request inspections and take legal action. It applies to sites that sell products or services online, take bookings or reservations, provide banking, transport or media services, or were updated or published after 28 June 2025. Exempt: companies with fewer than 10 employees and annual revenue under €2 million, except those selling products such as smartphones, tablets or e-readers. WCAG's four principles: perceivable, operable, understandable, robust. AI tools can flag missing alt text or contrast errors but cannot judge meaning; real accessibility needs human testing.
How to answer: plain English, short paragraphs, no markdown, no headings, no bullet symbols, no emoji. Two to four short paragraphs at most. Be accurate; if unsure, say so. This is general information, not legal advice. You cannot see or check any website from here: when someone wants a site checked, say the studio's free scanner runs a check at a11y-checker-v2-production.up.railway.app/demo.html, and that the studio offers a full audit by email at gf@barrierfreeweb.de. Never claim to have checked a site.`;

  const clean = (s) => s.replace(/\*\*|__|`/g, "").replace(/^#+\s*/gm, "").replace(/^\s*[-*•]\s+/gm, "");
  const fill = (el, text) => {
    el.replaceChildren();
    clean(text).split(/\n\s*\n/).map((x) => x.trim()).filter(Boolean).forEach((para) => {
      const p = document.createElement("p");
      p.textContent = para;
      el.append(p);
    });
  };
  const turn = (who, text) => {
    const li = document.createElement("li");
    li.className = "chat__turn";
    const label = document.createElement("p");
    label.className = "chat__who";
    label.textContent = who;
    const body = document.createElement("div");
    body.className = "chat__text";
    if (text) fill(body, text);
    li.append(label, body);
    thread.hidden = false;
    thread.append(li);
    return body;
  };
  const setBusy = (on) => {
    busy = on;
    send.disabled = on || !input.value.trim();
    prompts.querySelectorAll("button").forEach((b) => (b.disabled = on));
  };
  const grow = () => { input.style.height = "auto"; input.style.height = input.scrollHeight + "px"; };

  const ask = async (question) => {
    question = question.trim();
    if (!question || busy) return;
    prompts.hidden = true;
    turn("You", question);
    history.push({ role: "user", content: question });
    input.value = ""; grow();
    setBusy(true);
    const body = turn("Assistant", "");
    const thinking = document.createElement("p");
    thinking.className = "chat__thinking";
    thinking.textContent = "Thinking…";
    body.append(thinking);
    let answer = "";
    try {
      const sample = await samplePromise;
      if (!sample) throw { code: "unavailable" };
      const messages = history.map((m, i) => (i === 0 ? { role: m.role, content: FACTS + "\n\nQuestion: " + m.content } : m));
      const res = await sample(messages, { cache: false, onText: ({ text }) => { answer = text; if (text) fill(body, text); } });
      answer = res.text || answer;
      fill(body, answer);
      history.push({ role: "assistant", content: answer });
      status.textContent = "Assistant: " + clean(answer);
    } catch (e) {
      history.pop();
      const code = e && e.code;
      const msg = code === "rate_limited" ? "Too many questions at once. Wait a minute and ask again."
        : code === "not_granted" ? "Answers are turned off for this page in your settings. You can still write to us at gf@barrierfreeweb.de."
        : code === "unavailable" ? "The chat can’t answer in this view. Write to us at gf@barrierfreeweb.de and we’ll reply."
        : "The answer didn’t come through. Ask again, or write to us at gf@barrierfreeweb.de.";
      fill(body, (e && e.text) ? e.text + "\n\n" + msg : msg);
      status.textContent = "Assistant: " + msg;
    } finally {
      setBusy(false);
      input.focus();
    }
  };

  form.addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
  input.addEventListener("input", () => { grow(); send.disabled = busy || !input.value.trim(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });
  prompts.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));
  send.disabled = true;
});

// The contact line on the home page. The site has no server of its own, so
// the form writes an email to the studio in the visitor's mail app, with
// their address in it. Without this script the form's mailto action does
// the same, less tidily.
(() => {
  const form = document.querySelector("[data-signup]");
  if (!form) return;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const email = form.elements.email.value.trim();
    const subject = encodeURIComponent("Contact request");
    const body = encodeURIComponent(`Please get in touch with me at ${email}.`);
    window.location.href = `mailto:gf@barrierfreeweb.de?subject=${subject}&body=${body}`;
  });
})();
