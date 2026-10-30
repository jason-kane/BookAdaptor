(() => {
    const chapterKey = 'textoviva-director:' + location.pathname;

    function initializeDirector(element) {
        if (!element || element.dataset.directorInitialized) return;
        const config = JSON.parse(localStorage.getItem(chapterKey) || '{}');
        for (const field of ['brief', 'criteria']) {
            const input = element.querySelector('.director-' + field);
            input.value = config[field] || '';
            input.addEventListener('input', () => {
                config[field] = input.value;
                localStorage.setItem(chapterKey, JSON.stringify(config));
            });
        }
        const profile = element.querySelector('.director-profile');
        profile.value = config.profile || '';
        profile.addEventListener('change', () => {
            config.profile = profile.value;
            localStorage.setItem(chapterKey, JSON.stringify(config));
            if (element.dataset.imageIndex) {
                selectShot(element, element.dataset.imageIndex, element.dataset.frameIndex);
            }
        });
        element.dataset.directorInitialized = 'true';
    }

    function selectShot(element, index, frameIndex) {
        element.dataset.imageIndex = String(index);
        element.dataset.frameIndex = String(frameIndex);
        const dialogue = element.querySelector('.director-dialogue');
        dialogue.replaceChildren();
        const profile = element.querySelector('.director-profile').value;
        const historyKey = `${chapterKey}:shot:${index}:profile:${profile}`;
        const history = JSON.parse(localStorage.getItem(historyKey) || '[]');
        element.directorHistory = history;
        for (const item of history) {
            const line = document.createElement('p');
            line.textContent = (item.role === 'user' ? 'You: ' : 'Director: ') + item.content;
            dialogue.append(line);
        }
        element.querySelector('.director-status').textContent = `Editing Image ${index} · frame ${frameIndex}`;
        element.querySelector('.director-proposal').hidden = true;
        element.querySelector('.director-proposal').value = '';
        element.querySelector('.director-workflow').hidden = true;
        element.querySelector('.director-workflow').value = '';
        element.querySelector('.director-apply').hidden = true;
    }

    function initializeDirectors() {
        document.querySelectorAll('.textoviva-director').forEach(initializeDirector);
    }

    document.addEventListener('DOMContentLoaded', initializeDirectors);
    document.addEventListener('htmx:afterSwap', initializeDirectors);

    document.addEventListener('submit', async event => {
        const form = event.target.closest('.director-chat-form');
        if (!form) return;
        event.preventDefault();
        const element = form.closest('.textoviva-director');
        const messageField = form.querySelector('[name="message"]');
        const message = messageField.value.trim();
        if (!message) return;
        const profile = element.querySelector('.director-profile').value;
        if (!profile) {
            element.querySelector('.director-status').textContent = 'Choose a director profile first.';
            return;
        }

        const history = [...(element.directorHistory || [])];
        const button = form.querySelector('wa-button');
        const status = element.querySelector('.director-status');
        button.disabled = true;
        try {
            const chatUrl = element.dataset.chatUrlTemplate.replace(/\/0\/director$/, `/${element.dataset.imageIndex}/director`);
            const response = await fetch(chatUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    message,
                    history,
                    brief: element.querySelector('.director-brief').value,
                    profile,
                    criteria: element.querySelector('.director-criteria').value,
                    frame_index: element.dataset.frameIndex,
                }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Director request failed');

            const dialogue = element.querySelector('.director-dialogue');
            const userLine = document.createElement('p');
            userLine.textContent = 'You: ' + message;
            dialogue.append(userLine);
            const assistantText = [data.reply, data.follow_up_question].filter(Boolean).join('\n');
            const reply = document.createElement('p');
            reply.textContent = 'Director: ' + assistantText;
            dialogue.append(reply);
            history.push({role: 'user', content: message}, {role: 'assistant', content: assistantText});
            element.directorHistory = history;
            localStorage.setItem(
                `${chapterKey}:shot:${element.dataset.imageIndex}:profile:${profile}`,
                JSON.stringify(history)
            );
            status.textContent = data.changes_summary || '';
            element.dataset.currentAnimationPrompt = data.current_animation_prompt || '';
            element.dataset.currentI2vWorkflow = data.current_i2v_workflow || '';

            if (data.proposed_animation_prompt) {
                const proposal = element.querySelector('.director-proposal');
                proposal.value = data.proposed_animation_prompt;
                proposal.hidden = false;
                element.querySelector('.director-apply').hidden = false;
            }
            if (data.proposed_i2v_workflow) {
                const workflow = element.querySelector('.director-workflow');
                workflow.value = data.proposed_i2v_workflow;
                workflow.hidden = false;
                element.querySelector('.director-apply').hidden = false;
            }
            messageField.value = '';
        } catch (error) {
            status.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    });

    document.addEventListener('click', event => {
        const button = event.target.closest('.director-apply');
        if (!button) return;
        const element = button.closest('.textoviva-director');
        const index = element.dataset.imageIndex;
        const prompt = element.querySelector('.director-proposal').value
            || element.dataset.currentAnimationPrompt || ' ';
        const workflow = element.querySelector('.director-workflow');
        const applyUrl = element.dataset.applyUrlTemplate.replace(/\/0\/regenerate$/, `/${index}/regenerate`);
        htmx.ajax('POST', applyUrl, {
            target: '.director-status',
            swap: 'innerHTML',
            values: {
                animation_prompt: prompt || ' ',
                workflow_i2v_template: workflow.value,
            },
        });
    });

    document.addEventListener('textoviva:paused', event => {
        const {frameIndex, imageIndex} = event.detail;
        document.documentElement.dataset.playbackFrame = String(frameIndex);
        document.documentElement.dataset.playbackImageIndex = String(imageIndex);
        const director = document.querySelector('.textoviva-director');
        if (!director) return;
        initializeDirector(director);
        selectShot(director, imageIndex, frameIndex);
        director.open = true;
    });
})();
