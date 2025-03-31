import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from tqdm import tqdm
from iiif_prezi3 import Manifest, config, KeyValueString, CanvasRef
import base64


class CervantesPage:
    def __init__(self, url):
        self.url = url
        self.soup = self.__get_page_content()

    def __get_page_content(self):
        response = requests.get(self.url)
        if response.status_code == 200:
            return BeautifulSoup(response.text, "html.parser")
        else:
            raise Exception(f"Request to {self.url} returned {response.status_code}")

    def __get_image(self):
        img_tag = self.soup.find("img")
        if img_tag and "src" in img_tag.attrs:
            return img_tag.attrs["src"]
        else:
            print("No image found")
            return None

    def __get_metadata(self):
        rows = self.soup.find_all("tr")
        table_data = {}
        for row in rows:
            header = row.find("td", class_="header")
            data = row.find("td", class_="data")

            if header and data:
                header_text = header.get_text(strip=True)
                data_text = data.get_text(strip=True)
                table_data[header_text] = data_text
        return table_data

    def build_package(self):
        metadata = self.__get_metadata()
        identifier = self.url.split("&image=")[-1].split('.')[0]
        identifier_parts = identifier.split("-")
        range = f"{identifier_parts[0]}-{identifier_parts[1]}-{identifier_parts[2]}-{identifier_parts[3]}"
        if len(identifier_parts[-1]) == 3:
            part = identifier_parts[-1]
        else:
            part = f"{identifier_parts[-2]}-{identifier_parts[-1]}"
        image = self.__get_image()
        package = {
            "image": image,
            "based_image": self.base64_encode_url(image),
            "part": part,
            "range": range,
            "metadata": {},
        }
        for key, value in metadata.items():
            package["metadata"][key] = value
        return package

    @staticmethod
    def base64_encode_url(url):
        url_bytes = url.encode('utf-8')
        base64_bytes = base64.urlsafe_b64encode(url_bytes)
        return base64_bytes.decode('utf-8')


class CervantesWork:
    def __init__(self, url, **kwargs):
        self.url = url
        self.all_data = kwargs
        self.manifest_id = self.all_data.get("data", "manifest").get("id", "manifest")
        self.soup = self.__get_page_content(url)
        self.page_data = self.__get_pages()

    @staticmethod
    def __get_page_content(url):
        response = requests.get(url)
        if response.status_code == 200:
            return BeautifulSoup(response.text, "html.parser")
        else:
            raise Exception(f"Request to {url} returned {response.status_code}")

    def __get_pages(self):
        pg_nav = self.soup.find("div", id="pgNav")
        anchors = pg_nav.find_all("a")
        url = anchors[-1].get("href")
        parsed_url = urlparse(url)
        params = parse_qs(parsed_url.query)
        return {
            "partial": url,
            "params": params
        }

    def get_items(self):
        total_pages = self.page_data['params']["page"][0]
        i = 0
        all_items_in_work = []
        for page in range(1, int(total_pages)):
            i += 1
            get_results = self.__get_page_content(
                f"https://cervantes.library.tamu.edu/dqiDisplayInterface/{
                self.page_data['partial'].replace(
                    f'page={total_pages}', f'page={i}'
                )
                }"
            )
            all_anchors = get_results.find_all("a")
            print(f"Getting items from page {page}")
            for anchor in tqdm(all_anchors):
                href = anchor.get("href")
                if "displayMidImage.jsp?edition=" in href:
                    new_page = CervantesPage(f"https://cervantes.library.tamu.edu/dqiDisplayInterface/{href}")
                    package = new_page.build_package()
                    all_items_in_work.append(
                        {
                            'href': f"https://cervantes.library.tamu.edu/dqiDisplayInterface/{href}",
                            'range': self.find_range(href),
                            'package': package
                        }
                    )
        return all_items_in_work

    @staticmethod
    def find_range(relative_url):
        parts = relative_url.split('.jpg')[0].split('-')
        for i in range(len(parts) - 1):
            if len(parts[i + 1]) == 3:
                return parts[i]
        return None

    def build_manifest(self):
        all_items = self.get_items()
        config.configs['helpers.auto_fields.AutoLang'].auto_lang = "en"
        base_url = "https://markpbaggett.github.io/static_iiif/manifests/sample"
        label = self.all_data["data"]["title"] if 'title' in self.all_data["data"] else "Sample Manifest"
        # manifest_id = self.all_data["data"]["id"] if 'id' in self.all_data["data"] else "manifest"
        unprocessed_metadata = self.all_data["data"]["metadata"] if 'metadata' in self.all_data["data"] else {}
        metadata = []
        for k, v in unprocessed_metadata.items():
            metadata.append(
                KeyValueString(
                    label=k,
                    value=v,
                )
            )
        manifest = Manifest(
            id=f"{base_url}/{self.manifest_id}.json",
            label=label,
            metadata=metadata,
        )
        if 'based_thumb' in self.all_data["data"]:
            # print('yep')
            manifest.create_thumbnail_from_iiif(f"https://api-pre.library.tamu.edu/iiif/2/{self.all_data['data']['based_thumb']}")
        canvas_id = 0
        ranges = []
        range_id = 0
        for item in all_items:
            metadata = []
            for k, v in item['package']['metadata'].items():
                metadata.append(
                    KeyValueString(
                        label=k,
                        value=v
                    )
                )
            manifest.make_canvas_from_iiif(
                id=f"{base_url}/canvas/{canvas_id}",
                label=item["package"]["metadata"].get("Image", ""),
                url=f"https://api-pre.library.tamu.edu/iiif/2/{item['package']['based_image']}",
                metadata=metadata
            )
            if item['package']['range'] not in ranges:
                current_range = manifest.make_range(
                    id=f"{base_url}/range/{range_id}",
                    label=item['package']['range'],
                )
                range_canvas = CanvasRef(
                    id=f"{base_url}/canvas/{canvas_id}",
                    type="Canvas"
                )
                current_range.add_item(
                    range_canvas,
                )
                ranges.append(item['package']['range'])
                range_id += 1
            canvas_id += 1

        return manifest.json(indent=2)

    def write_manifest(self):
        try:
            with open(f"cervantes-manifests/{self.manifest_id}.json", "w") as manifest_file:
                manifest_file.write(self.build_manifest())
        except:
            pass


class CervantesEditionResults:
    def __init__(self, url):
        self.url = url
        self.content = self.__get_page_content(url)

    def __get_page_content(self, url):
        response = requests.get(url)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            target_table = soup.find_all("table")[2]
            trs = target_table.find_all("tr")
            data = []
            for tr in trs:
                anchors = tr.find_all("a")
                if len(anchors) > 0:
                    data.append({
                        "page": f"https://cervantes.library.tamu.edu/dqiDisplayInterface/{anchors[0].get("href")}",
                        "thumbnail": anchors[0].find_all("img")[0].get("src"),
                        "based_thumb": self.base64_encode_url(anchors[0].find_all("img")[0].get("src")),
                        "title": tr.find_all("td")[4].get_text(strip=True),
                        "metadata": {
                            "year": tr.find_all("td")[1].get_text(strip=True),
                            "place": tr.find_all("td")[2].get_text(strip=True),
                            "publisher": tr.find_all("td")[3].get_text(strip=True),
                            "author": tr.find_all("td")[5].get_text(strip=True),
                            "translator": tr.find_all("td")[6].get_text(strip=True),
                            "editor": tr.find_all("td")[7].get_text(strip=True),
                            "lang.": tr.find_all("td")[8].get_text(strip=True),
                            "vol.": tr.find_all("td")[9].get_text(strip=True),
                            "size": tr.find_all("td")[10].get_text(strip=True),
                            "index": tr.find_all("td")[11].get_text(strip=True),
                            "references": tr.find_all("td")[12].get_text(strip=True),
                            "library": tr.find_all("td")[13].get_text(strip=True)
                        },
                        "id": f"cervantes-{anchors[0].get("href").split("?id=")[-1].split("&")[0]}",
                    })
            return data
        else:
            raise Exception(f"Request to {url} returned {response.status_code}")

    def process(self):
        for content in self.content:
            if "https://cervantes.library.tamu.edu/dqiDisplayInterface/doSearchEditions.jsp" not in content['page']:
                print(f"Processing page {content['page']}")
                y = CervantesWork(
                    url=content.get("page"),
                    data=content
                )
                y.write_manifest()

    @staticmethod
    def base64_encode_url(url):
        url_bytes = url.encode('utf-8')
        base64_bytes = base64.urlsafe_b64encode(url_bytes)
        return base64_bytes.decode('utf-8')


if __name__ == "__main__":
    # expected_data = {'page': 'doSearchImages.jsp?id=490&page=1&orderBy=1', 'thumbnail': 'https://cervantes.library.tamu.edu/cervantes/DQIIMAGES/index/490.gif', 'based_thumb': 'aHR0cHM6Ly9jZXJ2YW50ZXMubGlicmFyeS50YW11LmVkdS9jZXJ2YW50ZXMvRFFJSU1BR0VTL2luZGV4LzQ5MC5naWY=', 'title': 'The Works of Laurence Sterne: The Life and Opinions of Tristram Shandy, Gentleman; Sermons; Mr. Yorick.', 'metadata': {'year': '1769', 'place': 'London', 'publisher': 'J. Dodsley', 'author': 'Laurence Sterne', 'translator': '', 'editor': '', 'lang.': 'ENG', 'vol.': '10', 'size': '12mo', 'index': 'e. Continuations and Imitations', 'references': 'R492', 'library': 'TAMU'}, 'id': 'cervantes-490'}
    # x = CervantesWork(
    #     "https://cervantes.library.tamu.edu/dqiDisplayInterface/doSearchImages.jsp?id=374&page=1&orderBy=1",
    #     data = expected_data
    # )
    # x.write_manifest()
    x = CervantesEditionResults(
    "https://cervantes.library.tamu.edu/dqiDisplayInterface/doSearchEditions.jsp?ftMode=phrase&ftFields=publisher&freeText=&year1=1500&year2=2100&places=All&languages=All&volumes=all&sizes=all&libraries=all&page=7&orderBy=1"
    )
    x.process()
