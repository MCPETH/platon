import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="platon",
    version="1.0.beta",
    author="Michael Zhang, Yayaati Chachan",
    author_email="zmzhang@caltech.edu",
    description="A package to compute transmission spectra and retrieve atmospheric parameters from transmission spectra",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/ideasrule/platon",
    download_url="https://github.com/ideasrule/platon/archive/beta.tar.gz",
    packages=setuptools.find_packages(),
    classifiers=(
        "Programming Language :: Python :: 2.7+, 3",
        "License :: GNU GPLv3 License",
        "Operating System :: OS Independent",
    ),
    include_package_data = True,
    zip_safe = False
)
