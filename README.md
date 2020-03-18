# bertfamily
- 个人博客：https://blog.csdn.net/xuyingjie125
- 用Keras实现更轻量级的bert及各种变种xbert



## 使用
安装方式：
```shell
pip install git+https://github.com/xuyingjie521/bertfamily.git
```
理论上兼容Python2和Python3，实验环境是Python 2.7、Tesorflow 1.13+以及Keras 2.2.4（也可在2.2.4、2.3.0、2.3.1、tf.keras下使用，建议试用稳定的2.2.4版本）

## 可加载权重
目前可支持加载的权重：
- <strong>Google原版bert</strong>: https://github.com/google-research/bert
- <strong>哈工大版roberta</strong>: https://github.com/ymcui/Chinese-BERT-wwm
- <strong>Google原版albert</strong>: https://github.com/google-research/ALBERT
- <strong>华为的NEZHA: https://github.com/huawei-noah/Pretrained-Language-Model/tree/master/NEZHA
- <strong>GPT2_ML: https://github.com/imcaspar/gpt2-ml

## 鸣谢
- 感谢CyberZHG大佬实现的keras-bert，本实现有不少地方参考了keras-bert的源码，在此衷心感谢大佬的无私奉献。
- 感谢苏神实现的bert，本实现有不少地方参考了其bert的源码，在此衷心感谢大佬的无私奉献。