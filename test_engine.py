import engine
import utils


def show_progress(progress):
    print(f"\r{progress['percent']:6.1f}%", end="", flush=True)


if __name__ == "__main__":
    url = input("Paste a video link: ")
    print(engine.get_info(url))
    print(engine.get_direct_link(url))
    result = engine.download(url, utils.get_downloads_folder(), "720p", show_progress)
    print()
    print(result)
