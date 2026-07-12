import os
import shutil
import const
from git import Repo
from github import Github
import tempfile

from flask import (
    Blueprint,
    render_template,
    request,
)

from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter


bp = Blueprint(
    "github",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

def as_branch_name(name):
    return name.lower().replace(' ', '_').replace('-', '_')


@bp.route("/save", methods=["POST"])
def save(author, title, chapter_number, language):
    chapter_number = int(chapter_number)
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    git_url = request.form.get("git_url")
    if chapter.config.get('git_url') != git_url:
        chapter.config['git_url'] = git_url
        chapter.save_config()

    github_token = request.form.get("github_token")

    with open(".github_token", "w") as h:
        h.write(github_token)
    
    commit_message = request.form.get("commit_message")

    # clone the repo to a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Repo.clone_from(git_url, tmpdir)
    
        new_branch = as_branch_name(f"{author.name}_{title}_chapter_{chapter_number}")
        repo.git.checkout('HEAD', b=new_branch)

        # copy the chapter's files into the repo
        # chapter directory
        repo_chapter_dir = os.path.join(
            tmpdir, 
            "library", 
            author.name, 
            title, 
            "chapter", 
            f"{chapter_number:04}"
        )

        os.makedirs(
            os.path.join(repo_chapter_dir, language),
            exist_ok=True
        )
        
        # git doesn't care about directories, we're fine there.
        for fn in [
            'camera_portrait.png',
            'camera_widescreen.png',
            'characters.json',
            'config.json',
            'cover.png',
            'masterplan.json',
            'text_layer_plain.png',
            'text_layer_rainbow.png',
            os.path.join(language, "chapter.txt"),
            os.path.join(language, "chapter.xml"),
        ]:
            if os.path.exists(os.path.join(const.LIBRARY_DIR, chapter.chapterdir, fn)):
                shutil.copy(
                    os.path.join(const.LIBRARY_DIR, chapter.chapterdir, fn),
                    os.path.join(repo_chapter_dir, fn)
                )

        for paragraph in chapter.paragraphs():
            paragraph['index'] = int(paragraph['index'])
            
            os.makedirs(
                os.path.join(
                    repo_chapter_dir,
                    "paragraphs",
                    f"{paragraph['index']:06}"
                ),
                exist_ok=True
            )
            # we only care about the images that are actually chosen
            for image_xml in paragraph.findAll('image'):
                shutil.copy(
                    os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "paragraphs", f"{paragraph['index']:06}", image_xml['src']),
                    os.path.join(repo_chapter_dir, "paragraphs", f"{paragraph['index']:06}", image_xml['src'])
                )

                prompt = image_xml.get('src', '').replace('.png', '.prompt')
                prompt_path = os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "paragraphs", f"{paragraph['index']:06}", prompt)

                if os.path.exists(prompt_path):
                    shutil.copy(
                        prompt_path,
                        os.path.join(repo_chapter_dir, "paragraphs", f"{paragraph['index']:06}", prompt)
                    )

            for phrase_xml in paragraph.findAll('phrase'):
                shutil.copy(
                    os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "paragraphs", f"{paragraph['index']:06}", phrase_xml['src']),
                    os.path.join(repo_chapter_dir, "paragraphs", f"{paragraph['index']:06}", phrase_xml['src'])
                )

                pronunciation = phrase_xml['src'] + ".pronunciation"
                pronunciation_path = os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "paragraphs", f"{paragraph['index']:06}", pronunciation)
                if os.path.exists(pronunciation_path):
                    shutil.copy(
                        pronunciation_path,
                        os.path.join(
                            repo_chapter_dir,
                            "paragraphs", 
                            f"{paragraph['index']:06}",
                            pronunciation
                        )
                    )
        
            # animation?
            # sources?
            # transitions?

        repo.index.add([repo_chapter_dir])
        
        # commit the changes
        repo.index.commit(commit_message)

        origin = repo.remote(name='origin')
        origin.push(new_branch)

        # make a PR from the commit to the main branch
        g = Github(github_token)
        #
        # git_url: "git@github.com:jason-kane/library.git"
        # we want "jason-kane/library"
        #
        github_repo = g.get_repo(git_url.split(':')[-1].replace('.git', ''))
        pr = github_repo.create_pull(
            title=commit_message,
            body=f"Automated pull request for {author.name} - {title} - Chapter {chapter_number}",
            head=new_branch,
            base="main"
        )

        # return a link to the pull request
        return f"Pull request created: {pr.html_url}"
       


@bp.route("/")
def base(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    
    github_token = ""
    if os.path.exists(".github_token"):
        with open(".github_token", "r") as h:
            github_token = h.read().strip()

    return render_template(
        "github.html",
        author=author,
        title=title,
        chapter_number=chapter_number,
        chapter=chapter,
        language=language,
        pretty_language=chapter.pretty_language,
        git_url=chapter.config.get('git_url', 'git@github.com:jason-kane/library.git'),
        github_token=github_token
    )
