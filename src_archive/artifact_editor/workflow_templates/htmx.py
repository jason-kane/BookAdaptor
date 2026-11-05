"""Unused workflow-template chat renderers; historical module."""

import markdown_it

import logger

log = logger.log(__name__)

md = markdown_it.MarkdownIt('commonmark', {'breaks': True, 'html': True})


def user_comment(prompt):
    log.warning('Obsolete code <WT0001>')
    prompt = md.render(prompt)
    return f"""
  <div class="comment wa-flank">
    <wa-avatar class="user-avatar" slot="media" shape="square" label="Square avatar">
        <wa-icon slot="icon" src="/static/fontawesome7/svgs/solid/smile.svg"></wa-icon>
    </wa-avatar>
    <div>{prompt}</div>
  </div>
  """


def ai_comment(message):
    log.warning('Obsolete code <WT0002>')
    message = md.render(message)
    return f"""
  <div class="comment wa-flank">
    <wa-icon class="ai-avatar" slot="icon" src="/static/fontawesome7/svgs/solid/brain.svg"></wa-icon>    
    <div>{message}</div>
  </div>
  """

