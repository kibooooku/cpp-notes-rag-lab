- 类和结构体除了可见性，其实没有本质区别
	- 可见性不同
		- 结构体struct的有成员默认为public
		- 类class所有成员<span style="color:rgb(255, 192, 0)">默认为private</span>
	- 其实结构体里也可以定义函数，也有构造函数
	- struct之所以保留只是为了兼容C的代码
	- C++代码风格来说，一般struct只作为多个变量的集中存放（但实际上当类用也可以），不参与继承等操作（虽然实际上也可以）
# static的三种用法
## 1.在类或结构体外的全局static
- 之前介绍过，保证C++在进行链接生成exe时只在内部使用这个函数
- `extern a`表示这个文件里a的值要在工程里其它cpp文件里找
- `static a = 10`表示这个a只在这个cpp文件里有效
## 2.在类或结构体外、作用域内的局部static
- 作用域内：如函数内、if for语句内等
- 作用1：作用域外无法访问
- 作用2：把变量的生命周期拉到永久，相当于定义时的赋值只会赋值一次（下例中，多次访问该函数，i的值递增而不是重复输出1）
	- 参见“5单例模式和局部静态”来进一步理解这个用法
```cpp
void Function() {
	static int i = 0;
	i++;
	std::cout << i << std::endl;
}
```
## 3.在类或结构体内
- 让这个变量或函数<span style="color:rgb(255, 192, 0)">和该类的所有实例共享内存</span> 
- 使用方式例：

非static变量：
```cpp
struct Entity {
    int x, y;
};
int main() {
    Entity e1{5, 10};
    Entity e2{1, 2};
}
```
static变量时（直接构造的方法编译不通过）：
- <span style="color:rgb(255, 192, 0)">相当于在Entity命名空间中创建了两个变量，他们实际上不属于类</span>
- 带 `::` 作用域限定符的变量定义**必须**出现在全局作用域（或命名空间作用域）中，不允许出现在函数体 `{}` 内部。
	- <span style="color:rgb(255, 77, 77)"><b>C++ 的单一定义规则（ODR，One Definition Rule）</b></span>
		- **一句话核心概括：** 在整个程序（所有 `.cpp` 文件加起来）中，**任何变量、函数、类、枚举、模板等实体，只能有且仅有一个“定义”（Definition）。可以有无数次“声明”（Declaration），但定义必须唯一。**
	- // C++17起有了 inline，在结构体static前加上inline，这就是定义，不需要再在 cpp 里写 int Entity::x;
		- 在 C++17 之前（也就是你下面这段代码的写法），类的静态成员必须在头文件（`.h`）里**声明**，再在源文件（`.cpp`）里**定义**。
		- 加上 `inline static int x = 0;` 后：
			- **放宽了 ODR 限制**：`inline` 告诉编译器：“这个变量可以有多个相同的定义（分散在不同的 `.cpp` 编译单元里），没事，你们各自编译，最后链接的时候**只保留一份副本**就行了。”
			- **内存唯一性**：虽然 `main.cpp` 和 `test.cpp` 编译时都看到了这份定义，但经过链接器的合并后，最终生成的程序中，`Entity::x` 在内存里依然**只有一个全局唯一的地址**。
	- 该部分总结：
		- 在 C++17 之前，类内的 `static int x;` 永远只是<span style="color:rgb(255, 77, 77)"><b>声明</b></span>。即使你写成 `static int x = 5;`（直接初始化），在没有 `inline` 的情况下，它依然只是声明加了一个默认初始化器，**依然不算定义**，必须在类外补 `int Entity::x;`。只有加上 `inline`（C++17 起），编译器才把这一行视为<span style="color:rgb(255, 77, 77)"><b>定义</b></span>，从而允许你省略类外定义。
- 静态方法不能访问非静态变量
	- 因为静态方法没有类实例
```cpp
struct Entity {
    static int x, y;
    static void print() {
        std::cout << "x: " << x << ", y: " << y << std::endl;
    }
};

int Entity::x; //如果在static前（或后）有inline，则本行和下一行可省略
int Entity::y;

int main() {
    Entity::x = 5;
    Entity::y = 10;

    Entity::print();
    return 0;  
}
```
