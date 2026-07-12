import markdown_it

md = markdown_it.MarkdownIt('commonmark', {'breaks': True, 'html': True})


def user_comment(prompt):
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
    message = md.render(message)
    return f"""
  <div class="comment wa-flank">
    <wa-icon class="ai-avatar" slot="icon" src="/static/fontawesome7/svgs/solid/brain.svg"></wa-icon>    
    <div>{message}</div>
  </div>
  """

